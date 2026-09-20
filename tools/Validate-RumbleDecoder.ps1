# Compare the analysis decoder against the unmodified generated guest function.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$VcVarsPath)
$ErrorActionPreference = 'Stop'
$workspace = Split-Path $PSScriptRoot -Parent
$build = Join-Path $workspace 'PS2Recomp/out/build'
$generated = Get-Content (Join-Path $workspace 'output-ghidra/Stream_DecompressChunk_0x127690.cpp') -Raw
$generated = $generated.Substring($generated.IndexOf('// Function:'))
$prefix = @'
#include <immintrin.h>
#include <cstdint>
#include <cstring>
#include <vector>
#include <fstream>
#include <iostream>
#include <iomanip>
#include <algorithm>
#include <stdexcept>
struct alignas(16) R5900Context { __m128i r[32]{}; uint32_t pc=0,branch_pc=0; bool in_delay_slot=false; };
struct PS2Runtime { bool eeCheckpointDue() { return false; } };
template<class T> T readMem(uint8_t* m,uint32_t a) { if(uint64_t(a)+sizeof(T)>128u*1024*1024) throw std::runtime_error("read bounds"); T v; std::memcpy(&v,m+a,sizeof v); return v; }
template<class T> void writeMem(uint8_t* m,uint32_t a,T v) { if(uint64_t(a)+sizeof(T)>128u*1024*1024) throw std::runtime_error("write bounds"); std::memcpy(m+a,&v,sizeof v); }
#define ADD32(a,b) uint32_t((a)+(b))
#define SUB32(a,b) uint32_t((a)-(b))
#define SLL32(a,b) uint32_t((a)<<(b))
#define SRL32(a,b) uint32_t((a)>>(b))
#define SRA32(a,b) uint32_t(int32_t(a)>>(b))
#define GPR_U32(c,i) ((i)==0?0u:uint32_t(_mm_cvtsi128_si32((c)->r[i])))
#define GPR_S32(c,i) int32_t(GPR_U32(c,i))
#define GPR_U64(c,i) ((i)==0?0ull:uint64_t(_mm_cvtsi128_si64((c)->r[i])))
#define GPR_S64(c,i) int64_t(GPR_U64(c,i))
#define GPR_VEC(c,i) ((i)==0?_mm_setzero_si128():(c)->r[i])
#define SET_GPR_U64(c,i,v) do { if((i)!=0) (c)->r[i]=_mm_unpacklo_epi64(_mm_cvtsi64_si128(int64_t(v)),_mm_srli_si128((c)->r[i],8)); } while(0)
#define SET_GPR_U32(c,i,v) SET_GPR_U64(c,i,int64_t(int32_t(v)))
#define SET_GPR_S32(c,i,v) SET_GPR_U32(c,i,v)
#define SET_GPR_VEC(c,i,v) do { if((i)!=0) (c)->r[i]=(v); } while(0)
#define READ8(a) readMem<uint8_t>(rdram,a)
#define READ16(a) readMem<uint16_t>(rdram,a)
#define READ128(a) readMem<__m128i>(rdram,a)
#define WRITE8(a,v) writeMem<uint8_t>(rdram,a,uint8_t(v))
#define WRITE16(a,v) writeMem<uint16_t>(rdram,a,uint16_t(v))
#define WRITE128(a,v) writeMem<__m128i>(rdram,a,v)
#define PS2_PCPYLD(rs,rt) _mm_unpacklo_epi64(rt,rs)
'@
$suffix = @'
uint32_t u32(const std::vector<uint8_t>& b,size_t p) { uint32_t v; std::memcpy(&v,b.data()+p,4); return v; }
int wmain(int argc,wchar_t**argv) { try {
 if(argc!=2) return 2;
 std::ifstream in(argv[1],std::ios::binary); if(!in) return 3;
 std::vector<uint8_t> b((std::istreambuf_iterator<char>(in)),{}), mem(128u*1024*1024);
 constexpr uint32_t dst=0x100000,src=112u*1024*1024;
 uint32_t written=0,expected=0,header=0; bool active=false; PS2Runtime rt;
 auto finish=[&] { if(!active) return; if(written!=expected) throw std::runtime_error("size mismatch");
   uint64_t hash=14695981039346656037ull; for(uint32_t i=0;i<expected;i++) hash=(hash^mem[dst+i])*1099511628211ull;
   std::cout<<std::dec<<header<<" "<<std::uppercase<<std::hex<<std::setw(16)<<std::setfill('0')<<hash<<"\n"; };
 for(size_t p=0;p+4<=b.size();) {
   uint32_t tag=u32(b,p); if(tag==0x46494c4c) {p=(p/0x6000+1)*0x6000;continue;}
   if(p+8>b.size()) throw std::runtime_error("header bounds");
   uint32_t n=u32(b,p+4); if(n<8||p+n>b.size()) throw std::runtime_error("block bounds");
   if(tag==0x53484f43 && n>=20) {
     uint32_t kind=u32(b,p+16);
     if(kind==0x53484452) { finish(); active=true; header=uint32_t(p); expected=u32(b,p+32); written=0;
       if(expected>100u*1024*1024) throw std::runtime_error("resource bounds"); std::memset(mem.data()+dst,0,expected); }
     else if(active && kind==0x53444154) {uint32_t take=std::min(n-20,expected-written);std::memcpy(mem.data()+dst+written,b.data()+p+20,take);written+=take;}
     else if(active && kind==0x52646174) {
       uint32_t count=u32(b,p+20); if(count>expected-written || n>0x6000) throw std::runtime_error("compressed bounds");
       std::memcpy(mem.data()+src,b.data()+p+24,n-24);
       R5900Context c; c.pc=0x127690; SET_GPR_U32((&c),4,src); SET_GPR_U32((&c),5,dst+written); SET_GPR_U32((&c),6,count); SET_GPR_U32((&c),29,127u*1024*1024);
       Stream_DecompressChunk_0x127690(mem.data(),&c,&rt);
       if(GPR_U32((&c),5)!=dst+written+count) throw std::runtime_error("guest decoder produced unexpected length");
       written+=count;
     }
   }
   p+=n;
 }
 finish(); return 0;
 } catch(const std::exception&e) {std::cerr<<e.what();return 1;} }
'@
$source = Join-Path $build 'validate-rumble-decoder.cpp'
[IO.File]::WriteAllText($source, $prefix + "`n" + $generated + "`n" + $suffix)
$vcvars = (Resolve-Path -LiteralPath $VcVarsPath).Path
Push-Location $build
try {
    # All paths in this fixed build command are local tool paths, not disc metadata.
    [IO.File]::WriteAllText((Join-Path $build 'decoder-validation-build.cmd'), "@echo off`r`ncall `"$vcvars`" >nul`r`nif errorlevel 1 exit /b 1`r`ncl /nologo /std:c++17 /EHsc /O2 validate-rumble-decoder.cpp /Fe:validate-rumble-decoder.exe`r`n")
    & cmd.exe /d /c decoder-validation-build.cmd *> decoder-validation-build.log
    if ($LASTEXITCODE -ne 0) { Get-Content decoder-validation-build.log -Tail 25; throw 'Reference decoder build failed' }
    $report=Get-Content (Join-Path $workspace 'analysis/assets-investigation.json') -Raw | ConvertFrom-Json
    $checked=0
    foreach($game in $report.Builds) {
        foreach($archive in $game.Archives) {
            $output = & .\validate-rumble-decoder.exe (Join-Path $game.Root $archive.Path)
            if($LASTEXITCODE -ne 0) { throw "Reference decoder failed: $($game.Build) $($archive.Path) $output" }
            $byOffset=@{}
            foreach($line in $output) { $parts=$line.Split(' '); $byOffset[[int]$parts[0]]=$parts[1] }
            if($byOffset.Count -ne $archive.Resources.Count) { throw 'Resource count mismatch' }
            foreach($resource in $archive.Resources) {
                if($resource.ValidationHash -ne $byOffset[[int]$resource.Offset]) { throw "Decoder mismatch: $($game.Build) $($archive.Path) 0x$('{0:X}' -f $resource.Offset)" }
                $checked++
            }
        }
    }
    $report | Add-Member -Force NoteProperty DecoderValidation ([pscustomobject]@{Resources=$checked;Method='FNV-1a 64-bit decoded payload digests compared against the unchanged generated Stream_DecompressChunk_0x127690 function in an isolated native harness';Passed=$true})
    $report | ConvertTo-Json -Depth 14 | Set-Content -LiteralPath (Join-Path $workspace 'analysis/assets-investigation.json') -Encoding utf8
    "PASS: $checked decoded resource digests match the original recompiled guest decoder across all three builds."
} finally { Pop-Location }
