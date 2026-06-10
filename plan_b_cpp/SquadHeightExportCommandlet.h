// SquadHeightExportCommandlet.h
//
// PLAN B: native commandlet for when editor-Python tracing is too slow.
// Python does ~5-30k traces/s (each trace crosses the Python<->C++ boundary
// several times); native code with ParallelFor does millions/s, turning a
// 16M-trace map from hours into well under a minute.
//
// This is a SKELETON: it compiles against stock UE5 editor modules but has
// not been built against the Squad SDK. Drop both files into an editor-only
// module of the SDK project (or a small plugin), add the module deps listed
// in the .cpp header comment, build, then run:
//
//   UnrealEditor-Cmd.exe Squad.uproject -run=SquadHeightExport
//       -Map=/Game/Maps/Chora/Chora -Out=C:/exports/chora -Res=1.0
//       [-Mode=topmost|terrain_under_overhang] [-Channel=ECC_Visibility]

#pragma once

#include "CoreMinimal.h"
#include "Commandlets/Commandlet.h"
#include "SquadHeightExportCommandlet.generated.h"

UCLASS()
class USquadHeightExportCommandlet : public UCommandlet
{
	GENERATED_BODY()

public:
	USquadHeightExportCommandlet();

	//~ UCommandlet interface
	virtual int32 Main(const FString& Params) override;
};
