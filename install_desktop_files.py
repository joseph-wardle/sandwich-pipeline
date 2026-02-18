import subprocess
from pathlib import Path


def update_desktop_db():
    subprocess.run(
        ["update-desktop-database", str(Path.home() / ".local/share/applications")]
    )


def sync_launchers(desktop_file_path: Path):
    target_dir = Path.home() / ".local/share/applications"

    for desktop_file in desktop_file_path.glob("*.desktop"):
        target_file = target_dir / desktop_file.name

        # Create or update symlink
        if target_file.is_symlink() or target_file.exists():
            target_file.unlink()

        target_file.symlink_to(desktop_file)
        print(f"Synced: {desktop_file.name}")
    update_desktop_db()
    print("Updated Desktop File Database")


if __name__ == "__main__":
    pipline_path = Path(__file__).resolve().parent
    sync_launchers(pipline_path / "desktop-files")
