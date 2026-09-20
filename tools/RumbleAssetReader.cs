using System;
using System.IO;
using System.Text;
using System.Collections.Generic;
using System.Security.Cryptography;

// Read-only analysis of the SHOC stream format, based on February's
// SM_ParseBufs (0x127A60), loadStreamChunk (0x127130), and
// Stream_DecompressChunk (0x127690). No extracted payload files are written.
public static class RumbleAssetReader
{
    public class TextHit { public int Offset; public string Text; }
    public class Reference { public string Type; public uint Id; public string Name; }
    public class Resource
    {
        public int Offset; public string Type; public uint Id; public int ExpectedBytes;
        public int DecodedBytes; public string SHA256; public string Error;
        public string ValidationHash;
        public string ResourceName;
        public List<Reference> References = new List<Reference>();
        public List<TextHit> Strings = new List<TextHit>();
        public List<string> Names = new List<string>();
    }
    public class Archive
    {
        public string Path; public int Bytes; public int Blocks;
        public Dictionary<string,int> Tags = new Dictionary<string,int>();
        public List<Resource> Resources = new List<Resource>();
        public List<string> Errors = new List<string>();
    }
    public static string Tag(byte[] b, int p)
    {
        return new string(new char[] {(char)b[p+3],(char)b[p+2],(char)b[p+1],(char)b[p]});
    }
    public static List<TextHit> Strings(byte[] b)
    {
        var hits = new List<TextHit>();
        int start = -1;
        for (int i=0;i<b.Length;i++)
        {
            if (b[i]>=32 && b[i]<=126) { if (start<0) start=i; }
            else {
                if (b[i]==0 && start>=0 && i-start>=4 && i-start<=200)
                    hits.Add(new TextHit {Offset=start,Text=Encoding.ASCII.GetString(b,start,i-start)});
                start=-1;
            }
        }
        return hits;
    }
    static int Decompress(byte[] src, int p, int end, byte[] dst, int offset, int length)
    {
        int target=checked(offset+length);
        if (length<0 || target>dst.Length) throw new InvalidDataException("Decoded length exceeds resource");
        while (offset<target)
        {
            if(p+2>end) throw new InvalidDataException("Truncated compression token");
            int a=src[p++], b=src[p++], word=(a<<8)|b;
            if ((word & 0x8800)==0x8800)
            {
                int n=(a>>4)&7;
                if(n==0) {
                    n=word&0x7ff;
                    if(n==0 || p+n>end || offset+n>target) throw new InvalidDataException("Invalid literal length");
                    Buffer.BlockCopy(src,p,dst,offset,n); p+=n; offset+=n;
                } else {
                    int distance=n|((word>>5)&0x38); n=b+3;
                    if(distance>offset || offset+n>target) throw new InvalidDataException("Invalid repeat");
                    byte value=dst[offset-distance];
                    for(int i=0;i<n;i++) dst[offset++]=value;
                }
            } else {
                int n=(a>>4)&7;
                if(n==7) { if(p>=end) throw new InvalidDataException("Missing extended length"); n=src[p++]+7; }
                n+=3;
                int distance=word&0xfff;
                int from=offset-distance;
                bool reverse=(word&0x8000)!=0;
                if(reverse) from+=2;
                if(offset+n>target || from<0 || from>=offset || (reverse && from-(n-1)<0))
                    throw new InvalidDataException("Invalid dictionary copy");
                for(int i=0;i<n;i++) { dst[offset++]=dst[from]; from+=reverse ? -1 : 1; }
            }
        }
        return length;
    }
    static void Finish(Resource r, byte[] data)
    {
        if(r==null || r.Error!=null) return;
        if(r.DecodedBytes!=r.ExpectedBytes) { r.Error="Incomplete resource"; return; }
        using(var sha=SHA256.Create()) r.SHA256=BitConverter.ToString(sha.ComputeHash(data)).Replace("-","");
        ulong digest=14695981039346656037UL;
        foreach(byte value in data) digest=unchecked((digest^value)*1099511628211UL);
        r.ValidationHash=digest.ToString("X16");
        // Keep identifiable paths/names, not millions of random printable image bytes.
        foreach(var s in Strings(data))
            if(s.Text.IndexOf(':')>=0 || s.Text.IndexOf(".TRK",StringComparison.OrdinalIgnoreCase)>=0 ||
               s.Text.IndexOf("TEST",StringComparison.OrdinalIgnoreCase)>=0 ||
               s.Text.IndexOf("DEBUG",StringComparison.OrdinalIgnoreCase)>=0 ||
               r.Type=="RLst" || r.Type=="Cnet" || r.Type=="gmd ") r.Strings.Add(s);
        if(r.Type=="RLst") {
            if(data.Length<4 || 4L+32L*BitConverter.ToUInt32(data,0)!=data.Length)
                throw new InvalidDataException("RLst count/size mismatch at 0x"+r.Offset.ToString("X"));
            for(int p=4;p<data.Length;p+=32) {
                int n=0; while(n<24 && data[p+8+n]!=0) n++;
                var reference=new Reference {Type=Tag(data,p),Id=BitConverter.ToUInt32(data,p+4),Name=Encoding.ASCII.GetString(data,p+8,n)};
                r.References.Add(reference); r.Names.Add(reference.Name);
            }
        }
    }
    public static Archive Read(string path)
    {
        byte[] bytes=File.ReadAllBytes(path);
        var archive=new Archive {Path=path,Bytes=bytes.Length};
        Resource current=null; byte[] data=null;
        var names=new Dictionary<string,string>();
        int p=0;
        while(p+4<=bytes.Length)
        {
            string tag=Tag(bytes,p);
            // FILL can occupy only the final four bytes of a 0x6000-byte page;
            // SM_ParseBufs skips to the next page without reading a length.
            if(tag=="FILL") {
                archive.Blocks++;
                archive.Tags[tag]=archive.Tags.ContainsKey(tag)?archive.Tags[tag]+1:1;
                p=checked((p/0x6000+1)*0x6000);
                continue;
            }
            if(p+8>bytes.Length) { archive.Errors.Add("Truncated block header"); break; }
            int size=BitConverter.ToInt32(bytes,p+4);
            if(size<8 || (long)p+size>bytes.Length) { archive.Errors.Add("Bad block at 0x"+p.ToString("X")); break; }
            archive.Blocks++;
            archive.Tags[tag]=archive.Tags.ContainsKey(tag)?archive.Tags[tag]+1:1;
            if(tag=="SHOC" && size>=20)
            {
                string kind=Tag(bytes,p+16);
                archive.Tags[kind]=archive.Tags.ContainsKey(kind)?archive.Tags[kind]+1:1;
                if(kind=="SHDR")
                {
                    Finish(current,data);
                    if(current!=null && current.Type=="RLst" && current.Error==null) {
                        names.Clear();
                        foreach(var reference in current.References) names[reference.Type+":"+reference.Id]=reference.Name;
                    }
                    if(size<56) { archive.Errors.Add("Short SHDR at 0x"+p.ToString("X")); break; }
                    current=new Resource {Offset=p,Type=Tag(bytes,p+24),Id=BitConverter.ToUInt32(bytes,p+28),ExpectedBytes=BitConverter.ToInt32(bytes,p+32)};
                    archive.Resources.Add(current);
                    string resourceName;
                    if(names.TryGetValue(current.Type+":"+current.Id,out resourceName)) current.ResourceName=resourceName;
                    if(current.ExpectedBytes<0 || current.ExpectedBytes>128*1024*1024) { current.Error="Implausible resource size"; data=null; }
                    else data=new byte[current.ExpectedBytes];
                }
                else if(current!=null && current.Error==null && (kind=="SDAT" || kind=="Rdat"))
                {
                    try {
                        if(kind=="SDAT") {
                            int take=Math.Min(size-20,data.Length-current.DecodedBytes);
                            Buffer.BlockCopy(bytes,p+20,data,current.DecodedBytes,take); current.DecodedBytes+=take;
                        } else {
                            if(size<24) throw new InvalidDataException("Short Rdat");
                            current.DecodedBytes+=Decompress(bytes,p+24,p+size,data,current.DecodedBytes,BitConverter.ToInt32(bytes,p+20));
                        }
                    } catch(Exception e) { current.Error="At block 0x"+p.ToString("X")+": "+e.Message; }
                }
            }
            p+=size;
        }
        Finish(current,data);
        if(p!=bytes.Length && archive.Errors.Count==0) archive.Errors.Add("Trailing bytes: "+(bytes.Length-p));
        return archive;
    }
}
