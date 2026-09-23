// Import names from the original February 7 Rumble Racing linker map.
// @category PS2Recomp
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.SourceType;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import java.util.regex.*;
import java.io.PrintWriter;

public class ImportRumbleXmap extends GhidraScript {
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 2) throw new IllegalArgumentException("Expected XMAP path and report path");
        String hash = currentProgram.getExecutableSHA256();
        if (!"44e74a35dd123e3504f81eded1db68844e2e082edaa908ea47a7ea1ec9a924a0".equalsIgnoreCase(hash))
            throw new IllegalStateException("This map is only validated for the February ELF; got " + hash);
        Pattern pattern = Pattern.compile("^\\s+([0-9A-Fa-f]{8})\\s+([0-9A-Fa-f]{8})\\s+(\\.[^\\s]+)\\s+(\\S+)\\s+\\((.*)\\)\\s*$");
        int renamed = 0, created = 0, labels = 0, conflicts = 0, trimmed = 0;
        Set<Long> processedFunctions = new HashSet<>();
        FunctionManager manager = currentProgram.getFunctionManager();
        java.util.List<String> mapLines = Files.readAllLines(Paths.get(args[0]), StandardCharsets.ISO_8859_1);
        // Ghidra can merge a shared tail into a different function. First trim known
        // functions to their linker-declared extent, freeing those tails for their
        // own linker-defined entry points. Never remove the function's entry point.
        for (String line : mapLines) {
            Matcher m = pattern.matcher(line);
            if (!m.matches() || !m.group(3).equals(".text")) continue;
            long value = Long.parseLong(m.group(1),16), size = Long.parseLong(m.group(2),16);
            if (size == 0 || m.group(4).startsWith(".") || m.group(4).startsWith("$") || m.group(4).startsWith("@")) continue;
            Address start = toAddr(value);
            Function f = manager.getFunctionAt(start);
            if (f == null) continue;
            AddressSet expected = new AddressSet(start, start.add(size - 1));
            AddressSet clipped = f.getBody().intersect(expected);
            if (clipped.contains(start) && clipped.getNumAddresses() < f.getBody().getNumAddresses()) {
                f.setBody(clipped);
                trimmed++;
            }
        }
        try (PrintWriter report = new PrintWriter(args[1], "UTF-8")) {
            report.println("February ELF SHA256: " + hash);
            for (String line : mapLines) {
                monitor.checkCancelled();
                Matcher m = pattern.matcher(line);
                if (!m.matches()) continue;
                long value = Long.parseLong(m.group(1), 16), size = Long.parseLong(m.group(2), 16);
                String section = m.group(3), name = m.group(4), source = m.group(5).trim();
                if (size == 0 || name.startsWith(".") || name.startsWith("$") || name.startsWith("@")) continue;
                Address start = toAddr(value);
                MemoryBlock block = currentProgram.getMemory().getBlock(start);
                if (block == null || !block.contains(start.add(size - 1))) continue;
                try {
                    if (section.equals(".text") && block.isExecute()) {
                        if (!processedFunctions.add(value)) continue;
                        Function function = manager.getFunctionAt(start);
                        if (function == null) {
                            if (manager.getFunctionContaining(start) != null) {
                                report.printf("OVERLAP %08X %s %s%n", value, name, source);
                                conflicts++;
                                continue;
                            }
                            AddressSet body = new AddressSet(start, start.add(size - 1));
                            if (manager.getFunctionsOverlapping(body).hasNext()) {
                                report.printf("OVERLAP %08X %s %s%n", value, name, source);
                                conflicts++;
                                continue;
                            }
                            disassemble(start);
                            function = manager.createFunction(name, start, body, SourceType.IMPORTED);
                            created++;
                        } else {
                            function.setName(name, SourceType.IMPORTED);
                            renamed++;
                        }
                        function.setComment("Original February XMAP: " + name + "\nObject/source: " + source +
                            "\nLinker size: 0x" + Long.toHexString(size));
                        report.printf("FUNCTION %08X %08X %s (%s)%n", value, size, name, source);
                    } else if (!section.equals(".text")) {
                        currentProgram.getSymbolTable().createLabel(start, name, SourceType.IMPORTED);
                        labels++;
                    }
                } catch (Exception e) {
                    report.printf("SKIPPED %08X %s: %s%n", value, name, e.getMessage());
                    conflicts++;
                }
            }
            String summary = String.format("Renamed %d functions; created %d; labeled %d data symbols; trimmed %d shared-tail bodies; conflicts/skips %d", renamed, created, labels, trimmed, conflicts);
            report.println(summary);
            println(summary);
        }
    }
}
