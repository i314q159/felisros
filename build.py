import hashlib
import os
import shutil
import tarfile
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "dist")
NAME = "felisros-linux"
COPY_FILES = ("app.py", "README.md", "build.py", "requirements.txt", "felisros.toml")
COPY_DIRS = ("core", "web", "examples", "packaging")


def ignored(path, names):
    return {name for name in names if name == "__pycache__" or name.endswith(".pyc")}


def clean(path):
    if os.path.isdir(path):
        shutil.rmtree(path)
    os.makedirs(path)


def digest(path):
    handle = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            handle.update(chunk)
    return handle.hexdigest()


def outputs():
    return (
        os.path.join(OUT, NAME + ".tar.gz"),
        os.path.join(OUT, NAME + ".zip"),
    )


def needs_build():
    tarball, archive_zip = outputs()
    if not os.path.isfile(tarball) or os.path.getsize(tarball) == 0:
        return True
    if not os.path.isfile(archive_zip) or os.path.getsize(archive_zip) == 0:
        return True
    built_at = os.path.getmtime(tarball)
    for name in COPY_FILES:
        path = os.path.join(ROOT, name)
        if os.path.isfile(path) and os.path.getmtime(path) > built_at:
            return True
    for name in COPY_DIRS:
        folder = os.path.join(ROOT, name)
        if not os.path.isdir(folder):
            continue
        for parent, dirs, files in os.walk(folder):
            dirs[:] = [item for item in dirs if item != "__pycache__"]
            for item in files:
                if item.endswith(".pyc"):
                    continue
                if os.path.getmtime(os.path.join(parent, item)) > built_at:
                    return True
    return False


def build():
    stage = os.path.join(OUT, NAME)
    clean(stage)
    app_root = os.path.join(stage, "app")
    os.makedirs(app_root)
    for name in COPY_FILES:
        shutil.copy2(os.path.join(ROOT, name), os.path.join(app_root, name))
    for name in COPY_DIRS:
        shutil.copytree(
            os.path.join(ROOT, name),
            os.path.join(app_root, name),
            ignore=ignored,
        )
    os.makedirs(os.path.join(stage, "bin"))
    layout = (
        ("launcher", os.path.join("bin", "felisros")),
        ("install.sh", "install.sh"),
        ("uninstall.sh", "uninstall.sh"),
        ("felisros.desktop", "felisros.desktop"),
    )
    for source, target in layout:
        shutil.copy2(os.path.join(ROOT, "packaging", source), os.path.join(stage, target))
    for name in ("install.sh", "uninstall.sh", os.path.join("bin", "felisros")):
        os.chmod(os.path.join(stage, name), 0o755)

    tarball, archive_zip = outputs()
    tarball_tmp = tarball + ".tmp"
    archive_zip_tmp = archive_zip + ".tmp"
    try:
        with tarfile.open(tarball_tmp, "w:gz") as archive:
            archive.add(stage, arcname=NAME)

        with zipfile.ZipFile(archive_zip_tmp, "w", zipfile.ZIP_DEFLATED) as archive:
            for folder, dirs, files in os.walk(stage):
                dirs[:] = sorted(dirs)
                for name in sorted(files):
                    full = os.path.join(folder, name)
                    archive.write(full, os.path.join(NAME, os.path.relpath(full, stage)))

        os.replace(archive_zip_tmp, archive_zip)
        os.replace(tarball_tmp, tarball)
    finally:
        for path in (tarball_tmp, archive_zip_tmp):
            if os.path.exists(path):
                os.remove(path)

    print("打包完成: " + tarball)
    print("备选格式: " + archive_zip)
    print("大小: {:.1f} KB".format(os.path.getsize(tarball) / 1024.0))
    print("SHA256: " + digest(tarball))
    return tarball


if __name__ == "__main__":
    build()
