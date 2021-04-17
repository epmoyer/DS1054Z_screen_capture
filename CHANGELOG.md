# CHANGELOG
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/en/1.0.0/) and this project adheres
to [Semantic Versioning](http://semver.org/spec/v2.0.0.html).

## 2.0.0u - 2021-04-15
This version is not yet released
### Changed
- Support Python 3.
- Discontinue support for Python 2.
- Hostname is now an option.
- Internal
    - Use click instead of argparse.
    - Refactor.
### Removed
- File type command line options (type is now exclusively `.png` for screenshot capture and `.csv` for data capture).
### Added
- Image annotation:
    - The following image "clutter" is automatically removed:
        - Left on-screen menu.
        - Right on-screen menu.
        - Upper left RIGOL logo.
        - Lower right status icons (sound, etc.)
    - The following annotation is automatically added:
        - Time/Date stamp (Upper left).
    - The following annotations are optionally added:
        - Note (`-n` option).
        - Signal Names (options `-1`, `-2`, `-3`, `-4`).
- Config file (`config.json`)

## 1.1.0 - 2017-01-04
- As of 2021-04-15 this was the most recent version of the original project:
    - https://github.com/RoGeorge/DS1054Z_screen_capture