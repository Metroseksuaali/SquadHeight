// SquadHeightExportCommandlet.cpp - see header for usage.
//
// Module dependencies (add to your editor module's Build.cs):
//   PublicDependencyModuleNames: "Core", "CoreUObject", "Engine"
//   PrivateDependencyModuleNames: "UnrealEd", "Landscape", "Foliage", "Json"
//
// NOTE: skeleton quality - the trace loop and filtering mirror the Python
// implementation in tools/export_heightmap.py (keep the two in sync!), but
// world bootstrapping in commandlets has engine-version-specific quirks.
// TODO markers indicate the spots to verify against the Squad SDK branch.

#include "SquadHeightExportCommandlet.h"

#include "Async/ParallelFor.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "FileHelpers.h"                       // UEditorLoadingAndSavingUtils
#include "Foliage/Public/InstancedFoliageActor.h"
#include "Landscape.h"
#include "LandscapeProxy.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "PhysicsEngine/PhysicsSettings.h"

DEFINE_LOG_CATEGORY_STATIC(LogSquadHeight, Log, All);

USquadHeightExportCommandlet::USquadHeightExportCommandlet()
{
	IsClient = false;
	IsServer = false;
	IsEditor = true;
	LogToConsole = true;
}

namespace
{
	struct FExportSettings
	{
		FString MapPackagePath;     // -Map=/Game/Maps/Chora/Chora
		FString OutDir;             // -Out=C:/exports/chora
		float   ResolutionM = 1.f;  // -Res=1.0
		bool    bTopmost   = true;  // -Mode=topmost (default) | terrain_under_overhang
		float   OverhangClearanceM = 2.5f;
		ECollisionChannel Channel = ECC_Visibility; // -Channel= (TODO: map Squad's custom channels)
	};

	bool IsFoliageHit(const FHitResult& Hit)
	{
		const AActor* Actor = Hit.GetActor();
		if (Actor && Actor->IsA<AInstancedFoliageActor>())
		{
			return true;
		}
		const UPrimitiveComponent* Comp = Hit.GetComponent();
		if (Comp)
		{
			// Landscape grass / foliage ISM components.
			static const FName FoliageISM(TEXT("FoliageInstancedStaticMeshComponent"));
			if (Comp->GetClass()->GetFName() == FoliageISM)
			{
				return true;
			}
			if (Actor && Actor->IsA<ALandscapeProxy>() &&
			    Comp->IsA<UInstancedStaticMeshComponent>())
			{
				return true; // grass on the landscape
			}
			// TODO: replicate the asset-path keyword filter from the Python
			// version if Squad has hand-placed foliage StaticMeshActors.
		}
		return false;
	}

	bool IsLandscapeGround(const FHitResult& Hit)
	{
		const AActor* Actor = Hit.GetActor();
		return Actor && Actor->IsA<ALandscapeProxy>() &&
		       !(Hit.GetComponent() &&
		         Hit.GetComponent()->IsA<UInstancedStaticMeshComponent>());
	}
}

int32 USquadHeightExportCommandlet::Main(const FString& Params)
{
	FExportSettings S;
	FParse::Value(*Params, TEXT("Map="), S.MapPackagePath);
	FParse::Value(*Params, TEXT("Out="), S.OutDir);
	FParse::Value(*Params, TEXT("Res="), S.ResolutionM);
	FString Mode;
	if (FParse::Value(*Params, TEXT("Mode="), Mode))
	{
		S.bTopmost = !Mode.Equals(TEXT("terrain_under_overhang"), ESearchCase::IgnoreCase);
	}
	if (S.MapPackagePath.IsEmpty() || S.OutDir.IsEmpty())
	{
		UE_LOG(LogSquadHeight, Error, TEXT("Usage: -run=SquadHeightExport -Map=/Game/... -Out=<dir> [-Res=1.0]"));
		return 1;
	}

	// ---- Load the map. In an editor commandlet the simplest reliable route
	// is the editor loading utils; they set up GWorld and the physics scene.
	// TODO(SquadSDK): verify this initializes world composition / streaming
	// proxies on Squad's large maps; call UWorld::FlushLevelStreaming after.
	if (!UEditorLoadingAndSavingUtils::LoadMap(S.MapPackagePath))
	{
		UE_LOG(LogSquadHeight, Error, TEXT("Failed to load %s"), *S.MapPackagePath);
		return 1;
	}
	UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : GWorld;
	check(World);
	World->FlushLevelStreaming(EFlushLevelStreamingType::Full);

	// ---- Bounds from landscape proxies (same rule as the Python version).
	FBox Bounds(ForceInit);
	for (TActorIterator<ALandscapeProxy> It(World); It; ++It)
	{
		Bounds += It->GetComponentsBoundingBox(true);
	}
	if (!Bounds.IsValid)
	{
		UE_LOG(LogSquadHeight, Error, TEXT("No landscape found; manual bounds not implemented in skeleton."));
		return 1;
	}

	const float StepCm  = S.ResolutionM * 100.f;
	const int32 NumCols = FMath::FloorToInt((Bounds.Max.X - Bounds.Min.X) / StepCm) + 1;
	const int32 NumRows = FMath::FloorToInt((Bounds.Max.Y - Bounds.Min.Y) / StepCm) + 1;
	const float ZTop    = Bounds.Max.Z + 20000.f; // +200 m
	const float ZBottom = Bounds.Min.Z - 10000.f; // -100 m
	UE_LOG(LogSquadHeight, Display, TEXT("Grid %dx%d @ %.2f m (%lld traces min)"),
	       NumCols, NumRows, S.ResolutionM, (int64)NumCols * NumRows);

	TArray<float> Heights;             // row-major, meters, NaN = no data
	Heights.SetNumUninitialized(NumRows * NumCols);

	FCollisionQueryParams QueryParams(SCENE_QUERY_STAT(SquadHeightExport),
	                                  /*bTraceComplex=*/true);
	// Foliage is filtered per-hit below; for the up-front fast path you can
	// also AddIgnoredActor every AInstancedFoliageActor here.
	for (TActorIterator<AInstancedFoliageActor> It(World); It; ++It)
	{
		QueryParams.AddIgnoredActor(*It);
	}

	// ---- The scan. ParallelFor over rows: scene queries are read-only and
	// thread-safe against an immutable physics scene.
	ParallelFor(NumRows, [&](int32 Row)
	{
		const float Y = Bounds.Min.Y + Row * StepCm;
		FCollisionQueryParams RowParams = QueryParams; // per-thread copy
		for (int32 Col = 0; Col < NumCols; ++Col)
		{
			const float X = Bounds.Min.X + Col * StepCm;
			float ChosenZ = TNumericLimits<float>::Lowest();
			float LandscapeZ = TNumericLimits<float>::Lowest();
			float PrevZ = ZTop;
			float StartZ = ZTop;
			bool bFound = false;

			for (int32 StepIdx = 0; StepIdx < 16; ++StepIdx)
			{
				FHitResult Hit;
				if (!World->LineTraceSingleByChannel(
					Hit, FVector(X, Y, StartZ), FVector(X, Y, ZBottom),
					S.Channel, RowParams))
				{
					break;
				}
				const float Z = Hit.Location.Z;
				if (!IsFoliageHit(Hit))
				{
					if (IsLandscapeGround(Hit))
					{
						LandscapeZ = Z;
						if (!bFound) { ChosenZ = Z; bFound = true; }
						else if (!S.bTopmost &&
						         PrevZ - Z >= S.OverhangClearanceM * 100.f)
						{
							// terrain_under_overhang: enough clearance below
							// the mesh -> prefer the ground.
							ChosenZ = Z;
						}
						break;
					}
					if (!bFound)
					{
						ChosenZ = Z; bFound = true;
						if (S.bTopmost) { break; }
						PrevZ = Z;
					}
					else if (!S.bTopmost && PrevZ - Z >= S.OverhangClearanceM * 100.f)
					{
						ChosenZ = Z; PrevZ = Z;
					}
				}
				StartZ = Z - 5.f; // re-trace 5 cm below the hit
				if (StartZ <= ZBottom) { break; }
			}

			if (!bFound && LandscapeZ > TNumericLimits<float>::Lowest())
			{
				ChosenZ = LandscapeZ; bFound = true;
			}
			Heights[Row * NumCols + Col] =
				bFound ? ChosenZ / 100.f : TNumericLimits<float>::QuietNaN();
		}
	});

	// ---- Normalize + write JSON (same format as the Python exporter:
	// 2D array of meters, min == 0, two decimals).
	float MinH = TNumericLimits<float>::Max(), MaxH = TNumericLimits<float>::Lowest();
	for (float H : Heights)
	{
		if (!FMath::IsNaN(H)) { MinH = FMath::Min(MinH, H); MaxH = FMath::Max(MaxH, H); }
	}
	FString Json;
	Json.Reserve(Heights.Num() * 6);
	Json += TEXT("[");
	for (int32 Row = 0; Row < NumRows; ++Row)
	{
		Json += TEXT("[");
		for (int32 Col = 0; Col < NumCols; ++Col)
		{
			float H = Heights[Row * NumCols + Col];
			H = FMath::IsNaN(H) ? 0.f : H - MinH;
			Json += FString::Printf(TEXT("%.2f"), H);
			if (Col != NumCols - 1) { Json += TEXT(","); }
		}
		Json += (Row != NumRows - 1) ? TEXT("],\n") : TEXT("]");
	}
	Json += TEXT("]");

	IFileManager::Get().MakeDirectory(*S.OutDir, true);
	const FString JsonPath = S.OutDir / TEXT("heightmap.json");
	FFileHelper::SaveStringToFile(Json, *JsonPath);
	UE_LOG(LogSquadHeight, Display,
	       TEXT("Done. min/max %.2f/%.2f m (offset %.2f m) -> %s"),
	       0.f, MaxH - MinH, MinH, *JsonPath);
	// TODO: 16-bit PNG + meta.json like the Python version (FImageUtils /
	// IImageWrapperModule "PNG" with ERGBFormat::Gray, 16-bit).

	return 0;
}
