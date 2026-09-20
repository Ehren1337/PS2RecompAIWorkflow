// Apply the user's saved PS2 analysis settings before headless analysis.
// @category PS2Recomp
import ghidra.app.script.GhidraScript;

public class ConfigurePS2Analysis extends GhidraScript {
    @Override
    public void run() throws Exception {
        setAnalysisOption(currentProgram, "Demangler GNU.Use Deprecated Demangler", "true");
        setAnalysisOption(currentProgram, "Non-Returning Functions - Discovered", "false");
        setAnalysisOption(currentProgram, "Non-Returning Functions - Known", "false");
        setAnalysisOption(currentProgram, "STABS", "true");
        println("Configured PS2 analysis for " + currentProgram.getLanguageID());
    }
}
