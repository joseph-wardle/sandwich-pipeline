# sandwich-pipeline

An OS-agnostic, portable, extensible 3D pipeline for the BYU Center for Animation's 2027 Capstone film, *Sandwich Kwon Do*.

`sandwich-pipeline` is currently being used on EL9 and Windows 11 systems. It should also be functional on macOS systems, but that has not been tested.

## Repo structure
```
sandwich-pipeline/
├── desktop_launchers      # .desktop files for the DCC launchers; installed via install_desktop_launchers.py
├── install_desktop_launchers.py
├── LICENSE
├── resources              # First-party non-Python data (OCIO, USD kinds, icons, splash, hdri, tex, sbs)
├── src
│   ├── env.py.md         # How to set up env.py
│   ├── __main__.py       # Entry point; dispatches to dcc.<name>.launch.<Dcc>Launcher
│   ├── sitecustomize.py
│   ├── framework         # DCCLauncher / DCCRuntime ABCs + dispatch + concrete launcher base
│   ├── core              # Cross-DCC platform code (asset, shot, versioning, telemetry, etc.)
│   └── dcc               # Per-DCC integrations (launch.py + runtime.py + site/ + third_party/)
├── telemetry-backend     # Postgres/Grafana configuration
├── pyproject.toml
└── README.md
```

## Setting up a copy of `sandwich-pipeline`
1. Fork this repo and clone it to the production location.
1. Create an `src/env.py` file following the specifications in `src/env.py.md`. This will get things like ShotGrid auth set up, and provide OS-specific DCC executable paths.
1. Run `uv sync` (or `.githooks/update-venv`) to set up the project environment.
1. Clone branches for development locally, copy over the env files and get to work!

## Setting up a dev environment in the labs
1. Generate a GitHub SSH key and upload it to your GitHub
   - ```bash
     ssh-keygen -t ed25519 -C "yourgithubemail@email.com"
     cat ~/.ssh/github.pub
     ```
   - When it asks for a path, type '/users/animation/yournetid/.ssh/github'
   - Only provide a passphrase if you want to type that every time you push or pull
   - Go to https://github.com/settings/keys and add the contents of `~/.ssh/github.pub` as a **New SSH key**
1. Make a local copy of the git repo
   ```bash
   cd ~/Documents
   git clone --recurse-submodules -c core.sshCommand='ssh -i ~/.ssh/github' git@github.com:joseph-wardle/sandwich-pipeline.git
   cd sandwich-pipeline
   ```
1. Configure the git repo to use the new SSH key and our git hooks
   ```bash
   git config --add --local core.sshCommand 'ssh -i ~/.ssh/github'
   git config --local core.hooksPath .githooks/
   ```
1. Check out a dev branch for the feature you are working on (or create a general dev branch (`yourname-dev`))
   ```bash
   git checkout -B feature-name-yourname 
   # don't need -B if it already exists
   git push --set-upstream origin feature-name-yourname
   ```

## Code Style

For this project, we are using the Black style of Python formatting. There is a Git pre-commit hook that will automatically run the `ruff` formatter on your code whenever you make a commit. If it changes any of your formatting it will print a message that looks like this:

```
Formatting with ruff...
3 files reformatted, 12 files left unchanged
```

After that, you can amend your commit to include the changes that `ruff` made with

```bash
git add <changed files here>
git commit --amend
```

This should generally be avoided, but if you need to override the Black style for some reason, use the following comments to suppress `ruff`:

```python
...
# fmt: off
unformatted code here
# fmt: on
...
```

## Type Checking

Type checking is enforced with `ty` in both pre-commit and CI.

Run it manually from repo root with:

```bash
uv run ty check --no-progress
```
