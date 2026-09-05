# Building Chordino (NNLS-Chroma) without sonic-annotator

`extract_chords.py` needs the Chordino Vamp plugin's `chordnotes` output.
The usual route is `sonic-annotator` + a prebuilt Chordino binary, both
distributed from `code.soundsoftware.ac.uk` - but that host was unreachable
from the dev sandbox this was built in (connection refused / no route on
both 80 and 443), and neither `sonic-annotator` nor Chordino/NNLS-Chroma are
packaged for Ubuntu. `sudo` also wasn't available there, ruling out
`apt-get install` for anything system-wide.

So instead of `sonic-annotator`, this builds just the Vamp plugin itself
from the official GitHub source mirrors (same Centre for Digital Music org
that publishes the soundsoftware.ac.uk downloads) and calls it directly from
Python via the `vamp` PyPI package (a thin ctypes host for Vamp plugins) -
same underlying plugin, same `chordnotes` output, no `sonic-annotator`
binary or system package involved. This mirrors the existing
`tools/adtlib-env` precedent in this repo (built ADTLib's TF1.15/Python 3.7
stack from source rather than fight a broken PyPI/apt path).

Both `vamp-plugin-sdk/` (cloned source + CMake build dir) and
`nnls-chroma/` (cloned source) under this directory, plus the compiled
`tools/vamp-plugins/nnls-chroma.so` one level up, are gitignored - rebuild
with the steps below if this ever needs to be reproduced elsewhere.

## Rebuild steps

```bash
cd tools/vamp-build

# 1. vamp-plugin-sdk: just need the static libs (vamp-sdk, vamp-hostsdk),
#    not the example plugins/hosts/RDF generator. Note: its CMakeLists.txt
#    has a bug where the example-plugins link step runs unconditionally
#    even with VAMPSDK_BUILD_EXAMPLE_PLUGINS=OFF, so build it ON here and
#    just don't use the result (harmless - it's a few extra seconds).
git clone --depth 1 https://github.com/c4dm/vamp-plugin-sdk.git
cmake -S vamp-plugin-sdk -B vamp-plugin-sdk/build -DCMAKE_BUILD_TYPE=Release \
    -DVAMPSDK_BUILD_EXAMPLE_PLUGINS=ON -DVAMPSDK_BUILD_SIMPLE_HOST=OFF -DVAMPSDK_BUILD_RDFGEN=OFF
cmake --build vamp-plugin-sdk/build -j"$(nproc)"
# (ignore an expected link failure on vamp-example-plugins.so if your path
# contains a space - libvamp-sdk.a / libvamp-hostsdk.a still get built fine)

# 2. nnls-chroma (contains Chordino): its own Makefile.linux assumes an
#    autotools-built SDK layout that the CMake build above doesn't produce,
#    so compile it directly instead of using make.
git clone --depth 1 https://github.com/c4dm/nnls-chroma.git
cd nnls-chroma
SDK=../vamp-plugin-sdk
for f in chromamethods NNLSBase NNLSChroma Chordino Tuning plugins viterbi; do
    g++ -O3 -fPIC -I"$SDK" -Wall -std=c++11 -c "$f.cpp" -o "$f.o"
done
gcc -O3 -fPIC -I"$SDK" -Wall -c nnls.c -o nnls.o   # nnls.c, not the Fortran nnls.f
g++ -shared -Wl,-soname=nnls-chroma.so -o nnls-chroma.so \
    chromamethods.o NNLSBase.o NNLSChroma.o Chordino.o Tuning.o plugins.o viterbi.o nnls.o \
    -L"$SDK/build" -lvamp-sdk -Wl,--version-script=vamp-plugin.map
cd ../..

# 3. Install where extract_chords.py expects it
mkdir -p vamp-plugins
cp vamp-build/nnls-chroma/nnls-chroma.so vamp-plugins/
```

## Verifying

```bash
cd ../..   # project root
VAMP_PATH="$(pwd)/tools/vamp-plugins" uv run python3 -c "
import vamp
print(vamp.list_plugins())              # expect nnls-chroma:chordino among these
print(vamp.get_outputs_of('nnls-chroma:chordino'))  # expect 'chordnotes' among these
"
```

`extract_chords.py` sets `VAMP_PATH` itself (pointing at `tools/vamp-plugins/`
relative to its own location), so no manual env var is needed for normal use -
this is just for checking the build worked.
