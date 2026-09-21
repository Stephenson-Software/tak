# @author Daniel McCoy Stephenson
"""Build the game.zip a game's Pyodide Worker downloads.

Everything the game needs to run in a tab goes in: the game's own source
tree(s), its JSON Schemas, whatever extra files it names (its version file,
its Pyodide entry point) - and the tak package itself, placed under src/tak
so the Worker's single sys.path entry (/game/src) covers both.

A game calls this from its own web/build_zip.py::

    from tak.web.bundle import build
    build(root=REPOSITORY_ROOT, extraFiles=("version.txt", "web/pyodide_main.py"))
"""

import os
import zipfile

import tak

OUTPUT_PATH = os.path.join("web", "game.zip")
SOURCE_DIRECTORIES = ("src", "schemas")


def _addTree(bundle, directory, archivePrefix):
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if name.endswith(".pyc"):
                continue
            path = os.path.join(root, name)
            archiveName = os.path.join(
                archivePrefix, os.path.relpath(path, directory)
            ).replace(os.sep, "/")
            bundle.write(path, archiveName)


def build(
    root,
    outputPath=OUTPUT_PATH,
    sourceDirectories=SOURCE_DIRECTORIES,
    extraFiles=(),
    includeTak=True,
):
    """Write the bundle. Paths in sourceDirectories/extraFiles are relative to
    root and are stored under the same relative paths, so the unpacked tree
    matches a checkout - which is what makes the game's cwd-relative schema
    paths resolve inside /game."""
    outputPath = os.path.join(root, outputPath)
    os.makedirs(os.path.dirname(outputPath), exist_ok=True)
    with zipfile.ZipFile(outputPath, "w", zipfile.ZIP_DEFLATED) as bundle:
        for directory in sourceDirectories:
            full = os.path.join(root, directory)
            if os.path.isdir(full):
                _addTree(bundle, full, directory)
        for relative in extraFiles:
            full = os.path.join(root, relative)
            if os.path.exists(full):
                bundle.write(full, relative.replace(os.sep, "/"))
        if includeTak:
            _addTree(bundle, os.path.dirname(os.path.abspath(tak.__file__)), "src/tak")
    print("Built %s" % outputPath)
    return outputPath
