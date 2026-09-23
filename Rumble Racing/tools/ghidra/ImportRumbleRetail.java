// Apply only individually verified retail names, never prototype addresses.
// @category PS2Recomp
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.address.AddressSet;
import ghidra.program.model.symbol.SourceType;
import java.security.MessageDigest;
import java.util.HexFormat;

public class ImportRumbleRetail extends GhidraScript {
    private void registerRetailCameraCallback() throws Exception {
        long start = 0x15a5f0;
        int size = 960;
        byte[] code = new byte[size];
        currentProgram.getMemory().getBytes(toAddr(start), code);
        String hash = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(code));
        if (!hash.equals("6b2cfbe8674e2d4f913cecd83aa8ed7f48ba2d3e1c6f6c057f600e3082b077a3"))
            throw new IllegalStateException("Retail camera callback bytes differ");
        Function function = getFunctionAt(toAddr(start));
        if (function == null) {
            AddressSet body = new AddressSet(toAddr(start), toAddr(start + size - 1));
            if (currentProgram.getFunctionManager().getFunctionsOverlapping(body).hasNext())
                throw new IllegalStateException("Retail camera callback overlaps another body");
            disassemble(toAddr(start));
            function = currentProgram.getFunctionManager().createFunction(
                "FUN_0015a5f0", toAddr(start), body, SourceType.USER_DEFINED);
        }
        if (function == null || function.getBody().getNumAddresses() != size)
            throw new IllegalStateException("Retail camera callback boundary differs");
        function.setComment("Retail entry observed at indirect call 0x15DF64. Complete 960-byte " +
            "stack frame, internal conditional branches, restoring epilogue/return; next entry 0x15A9B0. " +
            "February CameraAI_SimulateMountedWatchCamera at 0x15BF10 is a candidate only: " +
            "117/240 identical words, so its name is not transferred. Export the actual retail body.");
        println("Registered missing retail callback at " + toAddr(start));
    }

    private void repairPlayerNameLoop() throws Exception {
        long start = 0x1cb860;
        int size = 340;
        byte[] code = new byte[size];
        currentProgram.getMemory().getBytes(toAddr(start), code);
        String hash = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(code));
        if (!hash.equals("9fb3191dc557a5a2a4a665cae35502898e0b47088ac889e04b9d9215d6350b05"))
            throw new IllegalStateException("Retail player-name loop bytes differ");
        AddressSet body = new AddressSet(toAddr(start), toAddr(start + size - 1));
        var overlaps = currentProgram.getFunctionManager().getFunctionsOverlapping(body);
        while (overlaps.hasNext()) {
            Function function = overlaps.next();
            long entry = function.getEntryPoint().getOffset();
            AddressSet outside = new AddressSet(function.getBody());
            outside.delete(body);
            if ((entry != start && entry != 0x1cb9a0) || !outside.isEmpty())
                throw new IllegalStateException("Unexpected player-name loop overlap: " + function.getName());
        }
        currentProgram.getFunctionManager().removeFunction(toAddr(start));
        currentProgram.getFunctionManager().removeFunction(toAddr(0x1cb9a0));
        disassemble(toAddr(start));
        disassemble(toAddr(0x1cb868));
        currentProgram.getFunctionManager().createFunction("PN_vBuildPlayerNameTextureCLUT", toAddr(start), body, SourceType.USER_DEFINED);
        apply(start, size, hash, "PN_vBuildPlayerNameTextureCLUT",
            "0x1CF270; all 85 words identical, including four palette-component conversions, 16-entry loop and return. Rejoined incorrectly split entry/loop-test bodies");
    }

    private void repairViewportLoop() throws Exception {
        // Ghidra split the initial forward branch into a thunk and a loop-test
        // function whose body precedes its entry. The CSV exporter loses that
        // earlier block, so the generated guest stalls at the backward branch.
        long start = 0x16b120;
        int size = 60;
        byte[] code = new byte[size];
        currentProgram.getMemory().getBytes(toAddr(start), code);
        String hash = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(code));
        if (!hash.equals("6c619333f8573e2a984868a2c8891ab863a28d35225ab674368cc4aedb4bb83a"))
            throw new IllegalStateException("Retail viewport loop bytes differ");
        AddressSet body = new AddressSet(toAddr(start), toAddr(start + size - 1));
        var overlaps = currentProgram.getFunctionManager().getFunctionsOverlapping(body);
        while (overlaps.hasNext()) {
            Function function = overlaps.next();
            long entry = function.getEntryPoint().getOffset();
            AddressSet outside = new AddressSet(function.getBody());
            outside.delete(body);
            if ((entry != start && entry != 0x16b140) || !outside.isEmpty())
                throw new IllegalStateException("Unexpected viewport loop overlap: " + function.getName());
        }
        currentProgram.getFunctionManager().removeFunction(toAddr(start));
        currentProgram.getFunctionManager().removeFunction(toAddr(0x16b140));
        disassemble(toAddr(start));
        disassemble(toAddr(0x16b12c));
        currentProgram.getFunctionManager().createFunction("VM_vPrepareViewportMgr", toAddr(start), body, SourceType.USER_DEFINED);
        apply(start, size, hash, "VM_vPrepareViewportMgr",
            "0x16DA10; 14/15 identical words over the full 60-byte loop, including both stores, stride, count comparison and return; only GP-relative viewport-manager load relocates. Rejoined the incorrectly split thunk/loop-test bodies");
    }

    private void apply(long address, int size, String expectedHash, String name, String evidence) throws Exception {
        byte[] code = new byte[size];
        currentProgram.getMemory().getBytes(toAddr(address), code);
        String hash = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(code));
        if (!hash.equals(expectedHash)) throw new IllegalStateException("Retail bytes differ: " + name);
        Function function = getFunctionAt(toAddr(address));
        if (function == null) {
            AddressSet body = new AddressSet(toAddr(address), toAddr(address + size - 1));
            if (currentProgram.getFunctionManager().getFunctionsOverlapping(body).hasNext())
                throw new IllegalStateException("Verified retail function overlaps another body: " + name);
            disassemble(toAddr(address));
            function = currentProgram.getFunctionManager().createFunction(name, toAddr(address), body, SourceType.USER_DEFINED);
        }
        if (function == null || function.getBody().getNumAddresses() != size)
            throw new IllegalStateException("Retail function boundary differs: " + name);
        function.setName(name, SourceType.USER_DEFINED);
        function.setComment("Verified against February linker-named function: " + evidence +
            ". Same instruction/control-flow structure with relocated operands. See analysis/PORTING-STATUS.md.");
        println("Applied verified retail SDK name: " + name + " at " + toAddr(address));
    }

    public void run() throws Exception {
        if (!"e3c2c19b5fdeeac9fb1f5a9b893346e7892e564796fa2f74fc40ae17a8ade594"
                .equalsIgnoreCase(currentProgram.getExecutableSHA256()))
            throw new IllegalStateException("Verified USA retail ELF required");
        repairViewportLoop();
        repairPlayerNameLoop();
        registerRetailCameraCallback();
        apply(0x13f280, 556, "c7a5f72b8ed1db87a99793d7640ab4717dd2a28cc23b07d6380f76e7d314eaca",
            "RoadCaptainExtraAssembly_Update", "0x1428A0; all 139 words identical over the complete 556-byte function, including VU math, object field offsets, return and delay slot. Live indirect call at 0x13E388 confirms the omitted retail callback entry");
        apply(0x1cb780, 224, "aa2906fe7c734514215c738bb1f2a0be431ef8a6320d53b7a9ead5f18afce995",
            "PN_boTextureLookupCallback", "0x1CF190; 55/56 words identical over the complete callback. Only the GP-relative player-name texture-state load relocates (0xBC40 to 0xC3B0); identical resource-type comparison, texture descriptor packing, output pointers and return. Live indirect call at 0x12CB10 confirms the retail entry");
        apply(0x1c0440, 240, "eddff389a2b83244407c527d1d9f1455858e2910396f5b0e1bc20cd4c396349c",
            "SFXSurface_ConstantRadiusFunction", "0x1C3EE0; 59/60 identical words; sole difference is GP-relative sine-table pointer relocation (0x224 to 0x954), with identical indexed loads, branches, floating-point and VU operations");
        apply(0x1c1560, 76, "a0f41370c26c07119921b2d1d987b62864f3d79edab07420329cf5fa498a09d8",
            "vPrelitTriangleStripWithoutUVs_EndCurrentVUPacket", "0x1C5000; 18/19 identical words; sole difference is the GP-relative packet-state pointer load (0xBAF0 to 0xC260), preserving all packet writes and control flow");
        // Verified MPEG SDK family; HLE owns decoder references, never fixed guest globals.
        apply(0x107168, 168, "fe52a6e28de4c2a9c04f8a3fb11e751489c66be8102c82b69df193dd0098d133",
            "sceMpegInit", "0x108D48; full instruction comparison, matching control flow and structure offsets; reviewed relocated MPEG callees/globals and error string");
        apply(0x107210, 572, "4d0c72d36d22ba5d424bb025d7ee15daa8528d3a03d6b8750e0e434b1eaf0aaf",
            "sceMpegCreate", "0x108DF0; full instruction comparison, matching control flow and structure offsets; reviewed relocated MPEG callees/globals and error string");
        apply(0x107450, 8, "5ce5ad86d452c4d2422bd63e15223d5d6b3dfb77f224a88c1f476e9fb34e359d",
            "sceMpegDelete", "0x109030; full instruction comparison, matching control flow and structure offsets; reviewed relocated MPEG callees/globals and error string");
        apply(0x107458, 56, "019dd129020655142ae702a463b9c8a02202366ebc34acec46abd94dc2415d22",
            "sceMpegAddBs", "0x109038; full instruction comparison, matching control flow and structure offsets; reviewed relocated MPEG callees/globals and error string");
        apply(0x107490, 72, "84a7399fa4ea08798f5ac6b728468620a4059b0df3ca6a727c910d4114bda274",
            "sceMpegGetPicture", "0x109070; full instruction comparison, matching control flow and structure offsets; reviewed relocated MPEG callees/globals and error string");
        apply(0x1074d8, 68, "3cec7006ce7bb24df7c0fee6de4f149d63c6a596e35b61dc0f3f46347500e302",
            "sceMpegGetPictureRAW8", "0x1090B8; full instruction comparison, matching control flow and structure offsets; reviewed relocated MPEG callees/globals and error string");
        apply(0x107520, 80, "de8a4f0a3728eca91bde261853f6bea53cf07e5fa0bf28f14f52b4e79278ef53",
            "sceMpegGetPictureRAW8xy", "0x109100; full instruction comparison, matching control flow and structure offsets; reviewed relocated MPEG callees/globals and error string");
        apply(0x107570, 20, "c723762a67a1940b57482aac92c0025e5c8ca4b8d7dc4573024ba6ca11242922",
            "sceMpegSetDecodeMode", "0x109150; full instruction comparison, matching control flow and structure offsets; reviewed relocated MPEG callees/globals and error string");
        apply(0x107588, 32, "1c497288a1cced425d91d63b051586cc85474b2c04e8ebcee38cde6c9b01e6de",
            "sceMpegGetDecodeMode", "0x109168; full instruction comparison, matching control flow and structure offsets; reviewed relocated MPEG callees/globals and error string");
        apply(0x1075a8, 12, "f6dbf1b62e175f92f699849ff96e6f6fdebb979b319bb071a5933519e8a1260c",
            "sceMpegIsEnd", "0x109188; full instruction comparison, matching control flow and structure offsets; reviewed relocated MPEG callees/globals and error string");
        apply(0x1075b8, 16, "cd011e110122061d9478c94b9ff034ec338901e4b01cf93e42aac63190f4b8fd",
            "sceMpegIsRefBuffEmpty", "0x109198; full instruction comparison, matching control flow and structure offsets; reviewed relocated MPEG callees/globals and error string");
        apply(0x1075c8, 64, "9cf79634f63352a09f92b1b70a4e095a48e915196680219cd60ac49c7f597763",
            "sceMpegReset", "0x1091A8; full instruction comparison, matching control flow and structure offsets; reviewed relocated MPEG callees/globals and error string");
        apply(0x107608, 104, "227a30f733fc5f8f346ef1c8de00ad32ee84a8aa2054e5159f505856ecf2beb6",
            "sceMpegClearRefBuff", "0x1091E8; full instruction comparison, matching control flow and structure offsets; reviewed relocated MPEG callees/globals and error string");
        apply(0x107670, 36, "a3c969b4c8f4197cae0c8a7722bd029962d9f5d15af3dec2926cecd65e870ad2",
            "sceMpegAddCallback", "0x109250; full instruction comparison, matching control flow and structure offsets; reviewed relocated MPEG callees/globals and error string");
        apply(0x107710, 20, "4fd86ef4238220e9e5d4050fa202966f635dc984ac832b4a72655b973f77118c",
            "sceMpegSetDefaultPtsGap", "0x1092F0; full instruction comparison, matching control flow and structure offsets; reviewed relocated MPEG callees/globals and error string");
        apply(0x107728, 16, "bf5b1031fd5920b22a26a8f7a8f1d483eed27a489132853e00471eb193f8ead7",
            "sceMpegResetDefaultPtsGap", "0x109308; full instruction comparison, matching control flow and structure offsets; reviewed relocated MPEG callees/globals and error string");
        apply(0x107738, 20, "77390aaef7b80213cefb14d796dfeadb48afd0d3beca724aae519eac2be59b0d",
            "sceMpegSetImageBuff", "0x109318; full instruction comparison, matching control flow and structure offsets; reviewed relocated MPEG callees/globals and error string");
        apply(0x107750, 12, "74dcaa578e82c4ea515a7ea64331fa789a62c27cb02926b092c81bf8d4804c49",
            "sceMpegDispWidth", "0x109330; full instruction comparison, matching control flow and structure offsets; reviewed relocated MPEG callees/globals and error string");
        apply(0x107760, 12, "5a74c4bc4a6eb5a727ea14f1cc337cba7ff7ea04d88545a92e842b2789875916",
            "sceMpegDispHeight", "0x109340; full instruction comparison, matching control flow and structure offsets; reviewed relocated MPEG callees/globals and error string");
        apply(0x107770, 12, "8a9ca777a36f900dc7cb1c256e771d91909de86fe32a46807f45633761c5e746",
            "sceMpegDispCenterOffX", "0x109350; full instruction comparison, matching control flow and structure offsets; reviewed relocated MPEG callees/globals and error string");
        apply(0x107780, 12, "8a9ca777a36f900dc7cb1c256e771d91909de86fe32a46807f45633761c5e746",
            "sceMpegDispCenterOffY", "0x109360; full instruction comparison, matching control flow and structure offsets; reviewed relocated MPEG callees/globals and error string");
        apply(0x107cb8, 112, "f214d54c872127d5a1c6da825cde843cf2780c0af84475981889307696d64217",
            "sceMpegFlush", "0x109898; full instruction comparison, matching control flow and structure offsets; reviewed relocated MPEG callees/globals and error string");
        // Compared all 111 words with February sceSifInitRpc at 0x10D960.
        // 89 are identical; 22 differences are relocated data/code operands.
        // Checked SIF command IDs, callback setup and remote-register wait.
        apply(0x10bd60, 444, "2c5f95e7f2dd1baeca58d3f7ca57db99f8d64b17a86d5b4636ee45d18b776222",
            "sceSifInitRpc", "0x10D960; 89/111 identical words; retail live wait at 0x10BED8");
        apply(0x10b630, 24, "f1c7c0c10779ce1ee663ce71a9cc73d5adae12d1510a68021f59215a3ad90b74",
            "sceSifGetSreg", "0x10D230; same six instructions with two relocated table-address operands");
        apply(0x1b6100, 312, "67a344b6521aafe57338d5a64a3da29eba9cac7b24630dd115533ff5ba764cbc",
            "UAudioDMA_InitModule", "0x1B9C10; 56/78 identical words; only calls and gp-relative globals relocate; same buffer and register exchange");
        apply(0x1112d0, 744, "7d257c9150e99e8f3573278f971ea0a0bc2ac553899d725f77fd0bf6004d1f4a",
            "sceCdInit", "0x112ED0; 145/186 identical words; same CD RPC bind, error/exit strings and callbacks; retail wait 0x1113E0");
        apply(0x10ffc8, 748, "15d8a0221957d409b70a29c4f434f8d3a2a627c02d13623b5539e4d3e9044f7d",
            "sceCdSearchFile", "0x111BC8; 140/187 identical words; all branches/registers/RPC constants identical; SDK calls move by 0x1C00, printf by 0x1C48; relocated globals/strings; live SID 0x80000597");
        apply(0x1107d8, 452, "4680fd33559ceed300bb3d81073aae037dd4a5fa7796ce9abba7f2beb15a644f",
            "sceCdRead", "0x1123D8; 82/113 identical words; same sector/buffer/callback arguments and branches; reviewed SDK/libc calls and relocated globals/strings");
        apply(0x111050, 160, "7f1b1e96ccbc9d6701dd7f383a0f584b905e7453ef3241373f9b6ddaaa96a543",
            "sceCdSync", "0x112C50; 29/40 identical words; same poll/wait control flow and RPC check; reviewed relocated calls/globals/string");
        apply(0x10f928, 60, "447b60b8e932a445cc933b084692244f02516f7fd43e9c20def20d2a655f9801",
            "sceCdCallback", "0x111528; 12/15 identical words; same sync call and callback exchange; three address operands relocated");
        apply(0x1119b8, 152, "35f9ade59f3088768b54cfae38b954158095a9c14dd99ffbb2b28be2ccd57c76",
            "sceCdGetError", "0x1135B8; 30/38 identical words; same RPC/error/semaphore control flow; eight relocated SDK/global operands");
        apply(0x113028, 232, "59a76a2bd2929826c153562fb08d26583b1a9f214e7461501bbfa659cac04599",
            "sceMcGetDir", "0x114C28; 49/58 identical words; all branches/registers/constants match; relocated command state, RPC buffer and callback; strncpy moves by 0x1D28, SIF cache and RPC calls by 0x1C00");
        apply(0x10e410, 256, "abce13e79a359288a90affc7301c768e75b33fead66051d006449c6a147a447b",
            "sceSifRebootIop", "0x110010; 56/64 identical words; relocated reboot strings and SDK calls");
        apply(0x10e3c8, 72, "7a1673361ac27f7bb7a0538db1aa9c0cb06b9c54033138c2e6ac1ec084d80d9d",
            "sceSifSyncIop", "0x10FFC8; 15/18 identical words; only three relocated call targets");
        apply(0x10df30, 28, "8c3b4e3a58bc308ec1554e811deca7727c238300f26ce704957c19ed8ba4c120",
            "sceSifLoadModule", "0x10FB30; 6/7 identical words; call to matching internal loader 0x10DD30");
        apply(0x10c3f0, 320, "63f90a120243da7631e1f97eb0d9c7f07031c879bf5bf0ab18853283a143dbfe",
            "sceSifBindRpc", "0x10DFF0; 69/80 identical words; same client fields, semaphore flow and bind command");
        apply(0x10c5c0, 492, "194e07fd94346bb94249bd527fbd2fc14b05cfee5815d7ba967989e5e7adec48",
            "sceSifCallRpc", "0x10E1C0; 110/123 identical words; same RPC packet, mode and semaphore flow");
        apply(0x10b980, 44, "0bf803a38a91eccc4c650e3244bd3af09b8e914581ac3aee7ac2cacc3a6586eb",
            "sceSifAddCmdHandler", "0x10D580; 7/11 identical words; command table globals relocated");
        apply(0x10bb10, 60, "d727269fc3537274fa6d2453e2d52dbdec7a410f759a68963a30461f581a2f5f",
            "sceSifSendCmd", "0x10D710; 14/15 identical words; public wrapper calls internal helper 0x10B9D8");
        apply(0x10b9d8, 308, "3bb7f7d5b411a67e8749b13c4edbfcbda6aa34c281a01ccf5b572ca8e8779e82",
            "_sceSifSendCmd", "0x10D5D8; 71/77 identical words; seven-argument internal ABI with mode in a1, not public ABI");
        // Retail TTY/pad/CD startup matches reviewed against both ELFs.
        apply(0x10a8e0, 328, "12c091c29d37bcac304481c15567e4dc6bc34bd8938be4c177f471b0fa3422fc",
            "sceTtyWrite", "0x10C4E0; 74/84 identical words over full 336-byte extent; only SDK/libc calls and global/string addresses relocate");
        apply(0x111c10, 188, "1656d7618d97ae0d5cceabb89ed6597888290ec316d53b7fb977f4048bae5cf4",
            "sceCdMmode", "0x113810; 36/47 identical words over full 188-byte extent; only SDK/libc calls and global/string addresses relocate");
        apply(0x1115b8, 500, "dedba70a583d6eae60df825eedb61c7a108997a1573a7736d831f4d9a6bb83a4",
            "sceCdDiskReady", "0x1131B8; 93/126 identical words over full 504-byte extent; only SDK/libc calls and global/string addresses relocate");
        apply(0x111868, 152, "342d163cdc1574f15051fbda9617ec9e3a99642b4a419a1c1d259ce3af515870",
            "sceCdGetDiskType", "0x113468; 30/38 identical words over full 152-byte extent; only SDK/libc calls and global/string addresses relocate");
        apply(0x111900, 184, "5b1a6d86f3203cea86675d61946075da4df33222658ad68ef877a2126dd0ef82",
            "sceCdStatus", "0x113500; 34/46 identical words over full 184-byte extent; only SDK/libc calls and global/string addresses relocate");
        apply(0x10e640, 312, "72a354de07bdd7a789f479843f250615bad2f673f3aaefb4141e3e246d0cff11",
            "scePadInit", "0x110240; 67/80 identical words over full 320-byte extent; only SDK/libc calls and global/string addresses relocate");
        apply(0x10e780, 156, "efd1954816bfadb76e378dca1a5a653f4ad80804f1ac83f9cbecddaa520f4ce6",
            "scePadInit2", "0x110380; 34/39 identical words over full 156-byte extent; only SDK/libc calls and global/string addresses relocate");
        apply(0x10e8a0, 484, "65ceb9986d7dbc8c06f01eaa5cbd965e81c5ec044177b940acd16ae60a48a612",
            "scePadPortOpen", "0x1104A0; 105/121 identical words over full 484-byte extent; only SDK/libc calls and global/string addresses relocate");
        apply(0x10ea88, 184, "e6749104d1e2f674a8bbba6d85c2981c550d302e74547d1b594d6975c2564f9e",
            "scePadPortClose", "0x110688; 41/46 identical words over full 184-byte extent; only SDK/libc calls and global/string addresses relocate");
        apply(0x10ebf0, 124, "a234a6f1970116aa8a3c0c9475044f68f8aa5c81604088e81bb8d01c98abf05a",
            "scePadRead", "0x1107F0; 28/31 identical words over full 124-byte extent; only SDK/libc calls and global/string addresses relocate");
        apply(0x10ec70, 120, "22b4676d493f3421091dd10b9a646b0738085833d710cd98d482d3da26ffd575",
            "scePadGetState", "0x110870; 28/30 identical words over full 120-byte extent; only SDK/libc calls and global/string addresses relocate");
        apply(0x10ed88, 80, "a681aa9bc6955a734c78e8b063281f8c852cf5d903deb835406f7f469c2255fa",
            "scePadGetReqState", "0x110988; 18/20 identical words over full 80-byte extent; only SDK/libc calls and global/string addresses relocate");
        apply(0x10f050, 312, "748279e632fffd36e84b123e519396902bbb11ab2d4bdd040d562bb80f462aa9",
            "scePadInfoMode", "0x110C50; 76/78 identical words over full 312-byte extent; only SDK/libc calls and global/string addresses relocate");
        apply(0x10f188, 180, "a0822cee72102394a034b6a4d16ef22322b099ea9cd2599b2df8216ba081a77b",
            "scePadSetMainMode", "0x110D88; 40/45 identical words over full 180-byte extent; only SDK/libc calls and global/string addresses relocate");
        apply(0x10f240, 184, "d594c1ecf3f5819154897c9ffe7708c1829fe9954abe19ed358412bebebbe2d5",
            "scePadSetActDirect", "0x110E40; 43/46 identical words over full 184-byte extent; only SDK/libc calls and global/string addresses relocate");
        apply(0x10f2f8, 216, "2c8481f8207a939ae2d0b0748a44fdbdd82485b635ee320c3d703bb665955656",
            "scePadSetActAlign", "0x110EF8; 49/54 identical words over full 216-byte extent; only SDK/libc calls and global/string addresses relocate");
        apply(0x10f538, 92, "52262917e207be528c525da7ec7bec65a0a0ab59d4ee98305df15118efb1fdc3",
            "scePadInfoPressMode", "0x111138; 21/23 identical words over full 92-byte extent; only SDK/libc calls and global/string addresses relocate");
        apply(0x10f718, 100, "f31c3ae675c23834177eea1620ea3d74f094edcfbc9c6adf35e514e2c4f5e9d9",
            "scePadGetPortMax", "0x111318; 21/25 identical words over full 100-byte extent; only SDK/libc calls and global/string addresses relocate");
        apply(0x10f780, 104, "000cf38e2fd048712eb2e93520ba8715de1d32d1f1dd4e00abb0847aa2a277ca",
            "scePadGetSlotMax", "0x111380; 22/26 identical words over full 104-byte extent; only SDK/libc calls and global/string addresses relocate");
        apply(0x10f7e8, 100, "1ddd58caa288beb6e803791e3b3ea29ed01eb439b05fd8238ed13cb6e83f81da",
            "scePadGetModVersion", "0x1113E8; 21/25 identical words over full 100-byte extent; only SDK/libc calls and global/string addresses relocate");
        // GS and initial memory-card SDK paths, verified across both ELFs.
        apply(0x1000c0, 272, "51ea38d5a034f425cbcc657c6a5a89e160c6c26796b3547f7dab78d7d6ff75f1",
            "sceGsResetGraph", "0x100100; 63/68 identical words over 272-byte extent; reviewed relocated calls, tables and strings");
        apply(0x1001d0, 12, "a03677acb115e8cdb2a2686e27836e34797ad1b04c83dd56a42cc843d4a22430",
            "sceGsGetGParam", "0x100210; 2/3 identical words over 12-byte extent; reviewed relocated calls, tables and strings");
        apply(0x1001e0, 104, "38ec1ecd6a0a3a4763b12ebb5886e4af8ccaf54662cfa0b0d5e321d0e5864dd4",
            "sceGsResetPath", "0x100220; 25/26 identical words over 104-byte extent; reviewed relocated calls, tables and strings");
        apply(0x100248, 624, "aa3b9f13c9151c8a2e31c5e23bfbd72114992e322b61d52a06ce09e399c3a936",
            "sceGsSetDefDispEnv", "0x100288; 152/156 identical words over 624-byte extent; reviewed relocated calls, tables and strings");
        apply(0x1004b8, 188, "45fa5c9a27066181a08800a7a55bcf7cfd72ba09fdd32c47a5253a6e5ff80aeb",
            "sceGsPutDispEnv", "0x1004F8; 46/47 identical words over 188-byte extent; reviewed relocated calls, tables and strings");
        apply(0x100578, 200, "086989d5cd5b2f706cff87de79d715f21eaf134a65f99cd08df5e4dbca7b5ccd",
            "sceGszbufaddr", "0x1005B8; 49/50 identical words over 200-byte extent; reviewed relocated calls, tables and strings");
        apply(0x100640, 484, "39e7029deefccdc80432399ec583858f737b4956706f7a17e1d4a6d1f953e948",
            "sceGsSetDefDrawEnv", "0x100680; 119/121 identical words over 484-byte extent; reviewed relocated calls, tables and strings");
        apply(0x100828, 260, "6134a246928e1266165b881e4ae97cda6750e27cfd740585bfc8a744ff63aca2",
            "sceGsSetDefClear", "0x100868; 65/65 identical words over 260-byte extent; reviewed relocated calls, tables and strings");
        apply(0x100930, 232, "8e46d094c982f9142ceb7ab02ac714775aa13872cf46ae3cc7b10e425dedbc12",
            "sceGsPutDrawEnv", "0x100970; 55/58 identical words over 232-byte extent; reviewed relocated calls, tables and strings");
        apply(0x100a18, 148, "49ec393b8319bbbb6717d7ec4f950d9d7674fc9acc263fc8afabba6b95cf9405",
            "sceGsSyncV", "0x100A58; 34/37 identical words over 148-byte extent; reviewed relocated calls, tables and strings");
        apply(0x100ab0, 788, "d8f26fae31fe4cb3ef20925dac0d7d200c285373d6c73e2808621ade95efb720",
            "sceGsSyncPath", "0x100AF0; 156/197 identical words over 788-byte extent; reviewed relocated calls, tables and strings");
        apply(0x100dc8, 484, "8304371d797899173dd7cf3a19723026ebe1ca9c8528c6fb914d24fda42d55e2",
            "sceGsSetDefLoadImage", "0x100E08; 116/121 identical words over 484-byte extent; reviewed relocated calls, tables and strings");
        apply(0x100fb0, 320, "2790fd3d084174ae06d4dc038c973406032197f820f71c3988fee94a96764642",
            "sceGsSetDefStoreImage", "0x100FF0; 80/80 identical words over 320-byte extent; reviewed relocated calls, tables and strings");
        apply(0x1010f0, 380, "0a446294de417e206295f5250af029975839c6d2a661b7580a91ac2e0f667d60",
            "sceGsExecLoadImage", "0x101130; 92/95 identical words over 380-byte extent; reviewed relocated calls, tables and strings");
        apply(0x101270, 1676, "ca5db73cb78f218018e4f9b39676b982577a86a144fc1d68fad3ef582d979636",
            "sceGsExecStoreImage", "0x1012B0; 401/419 identical words over 1676-byte extent; reviewed relocated calls, tables and strings");
        apply(0x101900, 136, "8a6ee10c14649aba4de10207dca100efce88ffbff27d077ffb38d47edf2e4538",
            "sceGsSetHalfOffset", "0x101940; 34/34 identical words over 136-byte extent; reviewed relocated calls, tables and strings");
        apply(0x101988, 480, "59fd1501e2a1bbf04eb30beffb86a80e16fa0f68ad0ca88ce7666ac9f87fcd0f",
            "sceGsSetDefDrawEnv2", "0x1019C8; 118/120 identical words over 480-byte extent; reviewed relocated calls, tables and strings");
        apply(0x101b68, 136, "8a6ee10c14649aba4de10207dca100efce88ffbff27d077ffb38d47edf2e4538",
            "sceGsSetHalfOffset2", "0x101BA8; 34/34 identical words over 136-byte extent; reviewed relocated calls, tables and strings");
        apply(0x101bf0, 740, "462396492dd311cc0e33edc3926272d50282760352f6b2ab59857044d4a90d98",
            "sceGsSetDefDBuffDc", "0x101C30; 175/185 identical words over 740-byte extent; reviewed relocated calls, tables and strings");
        apply(0x101ed8, 92, "622bdbaedc41de9df32448397df86ef6ee267f1da917745a3b3bd51d813c3eea",
            "sceGsSwapDBuffDc", "0x101F18; 20/23 identical words over 92-byte extent; reviewed relocated calls, tables and strings");
        apply(0x112630, 340, "29c5ca27d542ac647af06717eeea37b358b8a371888a7cc61eb945ec2df5a736",
            "sceMcInit", "0x114230; 69/86 identical words over 344-byte extent; reviewed relocated calls, tables and strings");
        apply(0x112db0, 232, "e835af0608a7649936e2ef7c916b55e5acdb98b21d94d2ce4e0ad83ffb478a3e",
            "sceMcSync", "0x1149B0; 50/58 identical words over 232-byte extent; reviewed relocated calls, tables and strings");
        apply(0x112ef0, 308, "2e4ced440dff36d435b9d01c2d2ac5ef1f8e71aa45023e395655efc50b51728b",
            "sceMcGetInfo", "0x114AF0; 58/77 identical words over 308-byte extent; reviewed relocated calls, tables and strings");
    }
}
