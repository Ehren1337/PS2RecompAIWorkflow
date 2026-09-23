// Export decompiled prototype console/debug functions for comparison and restoration.
// @category PS2Recomp
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.listing.*;
import java.io.PrintWriter;

public class ExportRumbleDebug extends GhidraScript {
    public void run() throws Exception {
        String[] args = getScriptArgs();
        boolean query = args.length == 2 && args[0].equals("-");
        if (args.length != 1 && !query) throw new IllegalArgumentException("Expected output C file, or - functionName for a read-only console query");
        if (query && args[1].matches("asm:0x[0-9a-fA-F]+:[0-9]+")) {
            String[] fields = args[1].split(":");
            int count = Integer.parseInt(fields[2]);
            if (count < 1 || count > 128) throw new IllegalArgumentException("Instruction count must be 1..128");
            var address = toAddr(Long.parseUnsignedLong(fields[1].substring(2), 16));
            println(currentProgram.getName() + " SHA256=" + currentProgram.getExecutableSHA256());
            for (int i = 0; i < count; ++i) {
                var instruction = getInstructionAt(address);
                if (instruction == null) throw new IllegalArgumentException("No instruction at " + address);
                println(address + " " + instruction);
                address = address.add(instruction.getLength());
            }
            println("Instruction query complete");
            return;
        }
        if (query && args[1].startsWith("refs:0x")) {
            var address = toAddr(Long.parseUnsignedLong(args[1].substring(7), 16));
            int count = 0;
            for (var reference : getReferencesTo(address)) {
                if (++count > 128) { println("Reference output capped at 128"); break; }
                var owner = getFunctionContaining(reference.getFromAddress());
                println("Reference to " + address + " from " + reference.getFromAddress() +
                    " type=" + reference.getReferenceType() + " function=" +
                    (owner == null ? "<data/unassigned>" : owner.getName()));
            }
            println("Reference query complete: " + address);
            return;
        }
        DecompInterface decompiler = new DecompInterface();
        if (!decompiler.openProgram(currentProgram)) throw new IllegalStateException("Cannot open decompiler");
        if (query) {
            try {
                FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
                while (functions.hasNext()) {
                    Function function = functions.next();
                    if (!function.getName().equals(args[1])) continue;
                    println(currentProgram.getName() + " SHA256=" + currentProgram.getExecutableSHA256() +
                        " function=" + function.getName() + " at " + function.getEntryPoint());
                    DecompileResults result = decompiler.decompileFunction(function, 20, monitor);
                    println(result.decompileCompleted() ? result.getDecompiledFunction().getC() : result.getErrorMessage());
                    return;
                }
                throw new IllegalArgumentException("Function not found: " + args[1]);
            } finally { decompiler.dispose(); }
        }
        int count = 0;
        try (PrintWriter output = new PrintWriter(args[0], "UTF-8")) {
            output.println("/* Ghidra decompiler output, not original source. February Alpha 11.1. */");
            FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
            while (functions.hasNext()) {
                monitor.checkCancelled();
                Function function = functions.next();
                String name = function.getName();
                if (!(function.getComment() != null && function.getComment().contains("UStream.c")) && !name.startsWith("CO_") && !name.startsWith("VI_") && !name.startsWith("Stream_") && !name.startsWith("SM_") && !name.startsWith("File_") && !name.startsWith("UAudioDMA_") && !name.startsWith("UAudio_") && !name.startsWith("UAudEE_") && !name.startsWith("SifMgr_") && !name.startsWith("NL_") && !name.startsWith("Displ_") && !name.startsWith("Input_") && !name.equals("sceGsSetDefDispEnv") && !name.equals("sceMpegIsEnd") && !name.equals("ExceptionAlert") && !name.equals("sceGsSetDefDBuffDc") && !name.equals("sceGsSwapDBuffDc") && !name.equals("sceGszbufaddr") && !name.startsWith("sceGsSetDefLoadImage") && !name.startsWith("sceGsSetDefStoreImage") && !name.startsWith("sceGsExecLoadImage") && !name.startsWith("sceGsExecStoreImage") && !name.equals("boNoLoadingLoopFunc") && !name.equals("sceSifInitRpc") && !name.equals("main")) continue;
                output.printf("\n/* %s at %s; %s */\n", name, function.getEntryPoint(), function.getComment());
                DecompileResults result = decompiler.decompileFunction(function, 20, monitor);
                if (result.decompileCompleted()) output.println(result.getDecompiledFunction().getC());
                else output.println("/* Decompilation failed: " + result.getErrorMessage() + " */");
                count++;
            }
        } finally { decompiler.dispose(); }
        println("Exported " + count + " console/debug and startup functions");
    }
}
