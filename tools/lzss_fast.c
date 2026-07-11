// Fast LZSS compressor/decompressor matching LegaiaText reference semantics.
// Usage: lzss c <in> <out>   |   lzss d <in> <out> <declen>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned char *rd(const char *p, long *n) {
    FILE *f = fopen(p, "rb");
    if (!f) { perror(p); exit(1); }
    fseek(f, 0, SEEK_END); *n = ftell(f); fseek(f, 0, SEEK_SET);
    unsigned char *b = malloc(*n);
    if (fread(b, 1, *n, f) != (size_t)*n) { fprintf(stderr, "read fail\n"); exit(1); }
    fclose(f);
    return b;
}

static long compress(const unsigned char *data, long len, unsigned char *out) {
    unsigned char dic[0x1000];
    memset(dic, 0, sizeof(dic));
    long da = 4078, sa = 0, o = 0, bits_addr = 0;
    int mask = 0x80;
    unsigned char header = 0;

    while (sa < len) {
        mask <<= 1;
        if (mask == 0x100) {
            if (o > 0) out[bits_addr] = header;
            bits_addr = o;
            out[o++] = 0;
            header = 0;
            mask = 1;
        }

        int best_len = 2, best_pos = 0;
        if (sa + 2 < len) {
            for (int idx = 0; idx < 0x1000; idx++) {
                if (dic[idx] != data[sa]) continue;
                // simulate dictionary updates during the match
                unsigned char cmp[0x1000];
                memcpy(cmp, dic, 0x1000);
                long ca = da;
                int mlen = 0;
                for (int j = 0; j < 18 && sa + j < len; j++) {
                    if (cmp[(idx + j) & 0xFFF] != data[sa + j]) break;
                    mlen++;
                    cmp[ca] = data[sa + j];
                    ca = (ca + 1) & 0xFFF;
                }
                if (mlen > best_len) { best_len = mlen; best_pos = idx; }
            }
        }

        int length;
        if (best_len > 2) {
            out[o++] = best_pos & 0xFF;
            int nlo = (best_len - 3) & 0xF;
            int nhi = (best_pos >> 4) & 0xF0;
            out[o++] = nlo | nhi;
            length = best_len;
        } else {
            header |= (unsigned char)mask;
            out[o++] = data[sa];
            length = 1;
        }

        for (int i = 0; i < length; i++) {
            dic[da] = data[sa++];
            da = (da + 1) & 0xFFF;
        }
    }
    if (o > 0) out[bits_addr] = header;
    return o;
}

static long decompress(const unsigned char *data, long ia, long declen,
                       unsigned char *out, long avail) {
    unsigned char dic[0x1000];
    memset(dic, 0, sizeof(dic));
    long oa = 0, da = 4078;
    int mask = 0x80;
    unsigned char header = 0;
    while (oa < declen) {
        mask <<= 1;
        if (mask == 0x100) {
            if (ia >= avail) break;
            header = data[ia++];
            mask = 1;
        }
        if (header & mask) {
            if (ia >= avail) break;
            dic[da] = data[ia];
            out[oa++] = data[ia++];
            da = (da + 1) & 0xFFF;
        } else {
            if (ia + 1 >= avail) break;
            int v = data[ia] | (data[ia + 1] << 8);
            ia += 2;
            int length = ((v >> 8) & 0xF) + 3;
            int pos = ((v & 0xF000) >> 4) | (v & 0xFF);
            while (length-- > 0 && oa < declen) {
                dic[da] = dic[pos];
                out[oa++] = dic[pos];
                da = (da + 1) & 0xFFF;
                pos = (pos + 1) & 0xFFF;
            }
        }
    }
    return oa;
}

int main(int argc, char **argv) {
    if (argc < 4) { fprintf(stderr, "usage: lzss c|d in out [declen]\n"); return 1; }
    long n;
    unsigned char *in = rd(argv[2], &n);
    if (argv[1][0] == 'c') {
        unsigned char *out = malloc(n * 2 + 1024);
        long m = compress(in, n, out);
        FILE *f = fopen(argv[3], "wb");
        fwrite(out, 1, m, f);
        fclose(f);
        fprintf(stderr, "compressed %ld -> %ld\n", n, m);
    } else {
        long declen = atol(argv[4]);
        unsigned char *out = malloc(declen);
        long m = decompress(in, 0, declen, out, n);
        FILE *f = fopen(argv[3], "wb");
        fwrite(out, 1, m, f);
        fclose(f);
        fprintf(stderr, "decompressed -> %ld\n", m);
    }
    return 0;
}
