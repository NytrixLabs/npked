#!/usr/bin/env python3
import os
import sys
import json
import requests
import shutil
import argparse

pathexists = os.path.exists
repos = {}
packages = []

CONFIG_DIR = "/etc/npked"
INSTALL_DIR = "/usr/npked"
REPOS_FILE = f"{CONFIG_DIR}/repos"
PACKAGES_FILE = f"{CONFIG_DIR}/packages"


# ── Initialisation ────────────────────────────────────────────────────────────

def init():
    global repos, packages

    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(INSTALL_DIR, exist_ok=True)

    if pathexists(REPOS_FILE):
        with open(REPOS_FILE, "r") as f:
            repos = json.load(f)
    else:
        with open(REPOS_FILE, "w") as f:
            json.dump({}, f)

    if pathexists(PACKAGES_FILE):
        with open(PACKAGES_FILE, "r") as f:
            packages = json.load(f)
    else:
        with open(PACKAGES_FILE, "w") as f:
            json.dump([], f)


def _save_repos():
    with open(REPOS_FILE, "w") as f:
        json.dump(repos, f, indent=2)


def _save_packages():
    with open(PACKAGES_FILE, "w") as f:
        json.dump(packages, f, indent=2)


# ── Repo management ───────────────────────────────────────────────────────────

class Repos:
    def addrepo(self, name, url):
        url = url.rstrip("/")
        repos[name] = url
        _save_repos()
        print(f"Added repo '{name}' → {url}")

    def delrepo(self, name):
        if name in repos:
            del repos[name]
            _save_repos()
            print(f"Removed repo '{name}'")
        else:
            print(f"Repo '{name}' not found.")

    def listrepos(self):
        if not repos:
            print("No repos configured.")
            return
        for name, url in repos.items():
            print(f"  {name}  {url}")

    @staticmethod
    def getrepo(name):
        if name not in repos:
            return None
        try:
            r = requests.get(f"{repos[name]}/listing.json", timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            print(f"Failed to fetch repo '{name}': {e}")
            return None


# ── Package management ────────────────────────────────────────────────────────

class Packages:
    @staticmethod
    def getinstalled():
        return packages

    @staticmethod
    def getspecificpackage(name):
        for package in packages:
            if package["name"] == name:
                return package
        return None

    def search(self, query):
        """Search all repos for packages matching query."""
        query = query.lower()
        results = []
        for repo_name in repos:
            listing = Repos.getrepo(repo_name)
            if listing is None:
                continue
            for pkg in listing.get("packages", []):
                if query in pkg.get("name", "").lower() or query in pkg.get("description", "").lower():
                    results.append({**pkg, "_repo": repo_name})
        return results

    def _resolve(self, name):
        """Find the latest package metadata and repo URL for a package name."""
        for repo_name, repo_url in repos.items():
            listing = Repos.getrepo(repo_name)
            if listing is None:
                continue
            for pkg in listing.get("packages", []):
                if pkg.get("name") == name:
                    return pkg, repo_url
        return None, None

    def install(self, name, version=None):
        """Download and install a package from a remote repo."""
        if Packages.getspecificpackage(name):
            print(f"Package '{name}' is already installed. Use 'update' to upgrade.")
            return

        pkg_meta, repo_url = self._resolve(name)
        if pkg_meta is None:
            print(f"Package '{name}' not found in any repo.")
            return

        target_version = version or pkg_meta.get("version", "unknown")
        download_url = pkg_meta.get("url") or f"{repo_url}/packages/{name}-{target_version}.tar.gz"

        print(f"Installing {name} ({target_version}) from {repo_url} ...")

        install_path = os.path.join(INSTALL_DIR, name)
        os.makedirs(install_path, exist_ok=True)

        try:
            r = requests.get(download_url, timeout=30, stream=True)
            r.raise_for_status()
            archive_path = os.path.join(install_path, f"{name}.tar.gz")
            with open(archive_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

            # Extract archive
            import tarfile
            with tarfile.open(archive_path) as tar:
                tar.extractall(path=install_path)
            os.remove(archive_path)

            # Run install script if present
            install_script = os.path.join(install_path, "install.sh")
            if pathexists(install_script):
                os.chmod(install_script, 0o755)
                os.system(f"bash {install_script}")

        except requests.RequestException as e:
            print(f"Download failed: {e}")
            shutil.rmtree(install_path, ignore_errors=True)
            return
        except Exception as e:
            print(f"Installation failed: {e}")
            shutil.rmtree(install_path, ignore_errors=True)
            return

        packages.append({
            "name": name,
            "version": target_version,
            "repo": [r for r, u in repos.items() if u == repo_url][0],
            "install_path": install_path,
        })
        _save_packages()
        print(f"Installed {name} ({target_version}) successfully.")

    def uninstall(self, name):
        """Remove an installed package."""
        pkg = Packages.getspecificpackage(name)
        if pkg is None:
            print(f"Package '{name}' is not installed.")
            return

        install_path = pkg.get("install_path", os.path.join(INSTALL_DIR, name))
        if pathexists(install_path):
            # Run uninstall script if present
            uninstall_script = os.path.join(install_path, "uninstall.sh")
            if pathexists(uninstall_script):
                os.chmod(uninstall_script, 0o755)
                os.system(f"bash {uninstall_script}")
            shutil.rmtree(install_path)

        packages.remove(pkg)
        _save_packages()
        print(f"Uninstalled '{name}'.")

    def update(self, name=None):
        """Update one or all installed packages."""
        targets = [Packages.getspecificpackage(name)] if name else list(packages)
        targets = [p for p in targets if p]  # filter None

        if not targets:
            print("Nothing to update." if name else "No packages installed.")
            return

        for pkg in targets:
            pkg_name = pkg["name"]
            pkg_meta, repo_url = self._resolve(pkg_name)
            if pkg_meta is None:
                print(f"  {pkg_name}: not found in any repo, skipping.")
                continue

            latest = pkg_meta.get("version", "unknown")
            current = pkg.get("version", "unknown")

            if latest == current:
                print(f"  {pkg_name}: already up to date ({current}).")
                continue

            print(f"  {pkg_name}: {current} → {latest}")
            self.uninstall(pkg_name)
            self.install(pkg_name, version=latest)


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser():
    parser = argparse.ArgumentParser(
        prog="npked",
        description="npked — a simple package manager",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # repo subcommands
    repo_p = sub.add_parser("repo", help="Manage repositories")
    repo_sub = repo_p.add_subparsers(dest="repo_command", metavar="<action>")

    r_add = repo_sub.add_parser("add", help="Add a repo")
    r_add.add_argument("name")
    r_add.add_argument("url")

    r_del = repo_sub.add_parser("remove", help="Remove a repo")
    r_del.add_argument("name")

    repo_sub.add_parser("list", help="List configured repos")

    # package subcommands
    p_install = sub.add_parser("install", help="Install a package")
    p_install.add_argument("package")
    p_install.add_argument("--version", "-v", default=None)

    p_remove = sub.add_parser("remove", help="Uninstall a package")
    p_remove.add_argument("package")

    p_update = sub.add_parser("update", help="Update packages")
    p_update.add_argument("package", nargs="?", default=None,
                          help="Package to update (omit for all)")

    p_search = sub.add_parser("search", help="Search for packages")
    p_search.add_argument("query")

    sub.add_parser("list", help="List installed packages")

    return parser


def main():
    init()

    parser = build_parser()
    args = parser.parse_args()

    r = Repos()
    p = Packages()

    if args.command == "repo":
        if args.repo_command == "add":
            r.addrepo(args.name, args.url)
        elif args.repo_command == "remove":
            r.delrepo(args.name)
        elif args.repo_command == "list":
            r.listrepos()
        else:
            parser.parse_args(["repo", "--help"])

    elif args.command == "install":
        p.install(args.package, version=args.version)

    elif args.command == "remove":
        p.uninstall(args.package)

    elif args.command == "update":
        p.update(args.package)

    elif args.command == "search":
        results = p.search(args.query)
        if not results:
            print(f"No packages found for '{args.query}'.")
        else:
            print(f"Results for '{args.query}':")
            for pkg in results:
                print(f"  [{pkg['_repo']}] {pkg['name']} {pkg.get('version', '')}  —  {pkg.get('description', '')}")

    elif args.command == "list":
        installed = Packages.getinstalled()
        if not installed:
            print("No packages installed.")
        else:
            print("Installed packages:")
            for pkg in installed:
                print(f"  {pkg['name']}  {pkg.get('version', 'unknown')}  ({pkg.get('repo', '?')})")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()