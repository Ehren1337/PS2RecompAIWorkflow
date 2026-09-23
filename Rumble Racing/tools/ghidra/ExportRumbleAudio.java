// Export a verified IOP audio module into one reusable report per build.
// @category PS2Recomp
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.address.Address;
import ghidra.program.model.symbol.SourceType;
import java.io.PrintWriter;

public class ExportRumbleAudio extends GhidraScript {
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 1) throw new IllegalArgumentException("Expected output C file");
        String hash = currentProgram.getExecutableSHA256();
        boolean retail = "301f101c72c29ebecf368457b39de5a3ac65dbbdf2cff62e7b55470fb7e07f95".equalsIgnoreCase(hash);
        if (!retail && !"ce0981dd87c52f2253fe472e066237cdd574ca05c7dee3a460da4c190b8cc413".equalsIgnoreCase(hash))
            throw new IllegalStateException("Unverified AUDIO.IRX build");
        long threadAddress = retail ? 0x8dac : 0x856c;
        long commandAddress = retail ? 0x8f38 : 0x86f8;
        long importsAddress = retail ? 0xed80 : 0xd880;
        // The module entry passes this thread entry to thbase ordinal 4 (CreateThread).
        // Static analysis does not automatically follow that data reference.
        disassemble(toAddr(threadAddress));
        if (getFunctionAt(toAddr(threadAddress)) == null) createFunction(toAddr(threadAddress), "AudioThread");
        disassemble(toAddr(commandAddress));
        if (getFunctionAt(toAddr(commandAddress)) == null) createFunction(toAddr(commandAddress), "AudioCommandHandler");
        // Import ordinals verified against ps2dev/ps2sdk iop/system/sifcmd/include/sifcmd.h.
        String[] sifNames = {"sceSifInitCmd", "sceSifExitCmd", "sceSifGetSreg", "sceSifSetCmdBuffer",
            "sceSifAddCmdHandler", "sceSifRemoveCmdHandler", "sceSifSendCmd", "isceSifSendCmd"};
        for (int i = 0; i < sifNames.length; i++) {
            Address address = toAddr(importsAddress + i * 8);
            Function function = getFunctionAt(address);
            if (function != null) function.setName(sifNames[i], SourceType.USER_DEFINED);
        }
        DecompInterface decompiler = new DecompInterface();
        if (!decompiler.openProgram(currentProgram)) throw new IllegalStateException("Cannot open program");
        int count = 0;
        try (PrintWriter output = new PrintWriter(args[0], "UTF-8")) {
            output.println("/* " + (retail ? "USA retail / March" : "February") +
                " AUDIO.IRX: Ghidra analysis, not original source. Addresses are module-relative. */");
            output.println("/* SHA256 " + hash + " */");
            output.println("/* Import warning: stock ELF loader skipped .rel.text/.rel.rodata; verify address references against original bytes. */");
            for (MemoryBlock block : currentProgram.getMemory().getBlocks())
                output.printf("/* Block %s %s..%s */%n", block.getName(), block.getStart(), block.getEnd());
            FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
            while (functions.hasNext()) {
                monitor.checkCancelled();
                Function function = functions.next();
                output.printf("%n/* %s at %s */%n", function.getName(), function.getEntryPoint());
                for (ghidra.program.model.symbol.Reference ref : getReferencesTo(function.getEntryPoint())) {
                    Function caller = getFunctionContaining(ref.getFromAddress());
                    output.printf("/* XREF %s (%s) */%n", ref.getFromAddress(), caller == null ? "data/unassigned" : caller.getName());
                }
                DecompileResults result = decompiler.decompileFunction(function, 20, monitor);
                output.println(result.decompileCompleted() ? result.getDecompiledFunction().getC() : "/* FAILED: " + result.getErrorMessage() + " */");
                count++;
            }
        } finally { decompiler.dispose(); }
        println("Exported " + count + " IOP audio functions");
    }
}
