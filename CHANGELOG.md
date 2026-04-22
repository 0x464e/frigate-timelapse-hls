# Changelog

## [0.2.2](https://github.com/0x464e/frigate-timelapse-hls/compare/v0.2.1...v0.2.2) (2026-04-22)


### Bug Fixes

* properly deduplicate already ingested clips ([c649a38](https://github.com/0x464e/frigate-timelapse-hls/commit/c649a387f2ac8f89e6a5841bc56174d76b5a8711))


### Continuous Integration

* **release-please:** update version in uv.lock ([9e725c4](https://github.com/0x464e/frigate-timelapse-hls/commit/9e725c44fc886618bda624ddf4aec058071960d7))

## [0.2.1](https://github.com/0x464e/frigate-timelapse-hls/compare/v0.2.0...v0.2.1) (2026-04-21)


### Documentation

* **README:** mention that docker images are found on Docker Hub ([ca371fb](https://github.com/0x464e/frigate-timelapse-hls/commit/ca371fbc7174eb625edd340c99614663834394a1))


### Miscellaneous Chores

* **logs:** improve error reporting on failing to run ffmpeg ([91fd304](https://github.com/0x464e/frigate-timelapse-hls/commit/91fd3049e568e574022bfb05de2ef1dc79d4b4e8))


### Build System

* ditch cuda image in favor of plain debian image ([595b806](https://github.com/0x464e/frigate-timelapse-hls/commit/595b8067f51a9261d09343f8859d2f149aba3868))


### Continuous Integration

* **release-please:** unhide all changelog sections ([b431939](https://github.com/0x464e/frigate-timelapse-hls/commit/b431939c9ab09388d33890f5c4e48a4c6f594994))

## [0.2.0](https://github.com/0x464e/frigate-timelapse-hls/compare/v0.1.0...v0.2.0) (2026-04-21)


### Features

* **config:** ability to config behavior on resuming an interrupted timelapse generation session (resume or regenerate) ([1226db8](https://github.com/0x464e/frigate-timelapse-hls/commit/1226db84c3fff132dc82eb2abb7d2dabfd383775))
* **resuming:** ability to resume interrupted timelapse generation ([1226db8](https://github.com/0x464e/frigate-timelapse-hls/commit/1226db84c3fff132dc82eb2abb7d2dabfd383775))


### Documentation

* add initial README ([039ea11](https://github.com/0x464e/frigate-timelapse-hls/commit/039ea1175878c08490838c0f09008f87af71553c))

## 0.1.0 (2026-04-20)


### Bug Fixes

* **ci:** wrong encoding slipped into manifest json ([5360f12](https://github.com/0x464e/frigate-timelapse-hls/commit/5360f12fef6995cd263e906f4c25415639bb6210))
