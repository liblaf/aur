default:
    @just --list

# Run shared and package regression checks.
test:
    python3 scripts/aur.py test

# Check upstream releases, AUR synchronization, and validation age.
check package:
    python3 scripts/aur.py check {{quote(package)}}

# Download and validate upstream assets, then regenerate package metadata.
update package:
    python3 packages/{{quote(package)}}/scripts/update.py --update

# Build, install, and exercise a package in disposable Arch Linux.
validate package:
    python3 scripts/aur.py validate {{quote(package)}} --candidate .candidates/{{quote(package)}}

# Run the complete shared publishing workflow on GitHub.
publish:
    gh workflow run packages.yml --repo liblaf/aur -f validate_current=true
