// @category PS2Recomp
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.listing.*;
import java.io.PrintWriter;

public class ExportRumbleAssets extends GhidraScript {
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 1) throw new IllegalArgumentException("Expected output C file");
        String selection = "^(Stream_(OpenStreamFile|InitModule|DecompressChunk|GetDataElement|Progress|StartLoadingResource|DownLoadData|AssignStreamToMissionFile|RecordDebugResourcesList)|loadStreamChunk|SM_ParseBufs|Resources_.*|PhysicsTest_.*|RB_(InitResource|LoadResource|CarResourcesPreloading|vLoadTrack|vGameLoopHandleConsole|vInitModules|vRunMainLoop)|FE_(FindTrack|FindTrackDirectory|LoadCarResource|GetTrackLock|PrepareTrack)|CO_vRegister.*|CO_vInitModule|CO_vManage|CO_vAddAllToDisplayList)$";
        DecompInterface decompiler = new DecompInterface();
        if (!decompiler.openProgram(currentProgram)) throw new IllegalStateException("Cannot open program");
        int count = 0;
        try (PrintWriter output = new PrintWriter(args[0], "UTF-8")) {
            output.println("/* Ghidra analysis output, not original source. February Alpha 11.1. */");
            FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
            while (functions.hasNext()) {
                monitor.checkCancelled();
                Function function = functions.next();
                if (!function.getName().matches(selection)) continue;
                output.printf("\n/* %s at %s; %s */\n", function.getName(), function.getEntryPoint(), function.getComment());
                for (ghidra.program.model.symbol.Reference ref : getReferencesTo(function.getEntryPoint())) {
                    Function caller = getFunctionContaining(ref.getFromAddress());
                    output.printf("/* XREF from %s (%s), %s */\n", ref.getFromAddress(), caller == null ? "data or unassigned" : caller.getName(), ref.getReferenceType());
                }
                DecompileResults result = decompiler.decompileFunction(function, 30, monitor);
                output.println(result.decompileCompleted() ? result.getDecompiledFunction().getC() : "/* FAILED: " + result.getErrorMessage() + " */");
                count++;
            }
        } finally { decompiler.dispose(); }
        println("Exported " + count + " asset/debug functions");
    }
}
