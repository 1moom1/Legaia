// Compare two Mode2/2352 BINs sector by sector; report which sectors' USER data differs.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define RAW 2352
#define UOFF 24
#define USER 2048

int main(int argc, char **argv) {
    FILE *a = fopen(argv[1], "rb");
    FILE *b = fopen(argv[2], "rb");
    if (!a || !b) { perror("open"); return 1; }
    fseek(a, 0, SEEK_END);
    long size = ftell(a);
    fseek(a, 0, SEEK_SET);
    long nsec = size / RAW;
    unsigned char sa[RAW], sb[RAW];

    long diffs = 0;
    long run_start = -1, run_end = -1;
    for (long i = 0; i < nsec; i++) {
        if (fread(sa, 1, RAW, a) != RAW) break;
        if (fread(sb, 1, RAW, b) != RAW) break;
        int d = memcmp(sa + UOFF, sb + UOFF, USER) != 0;
        if (d) {
            diffs++;
            if (run_start < 0) run_start = i;
            run_end = i;
        } else {
            if (run_start >= 0) {
                printf("DIFF LBA %ld..%ld  (%ld sectors)\n",
                       run_start, run_end, run_end - run_start + 1);
                run_start = -1;
            }
        }
    }
    if (run_start >= 0)
        printf("DIFF LBA %ld..%ld  (%ld sectors)\n", run_start, run_end,
               run_end - run_start + 1);
    fprintf(stderr, "total differing sectors: %ld / %ld\n", diffs, nsec);
    return 0;
}
