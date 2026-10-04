# CHANGELOG

<!-- version list -->

## v0.3.0 (2026-10-04)

### Documentation

- Add a CI guide for regress check with a GitHub Actions workflow
  ([`b5f4c5a`](https://github.com/Dino-Kupinic/regress/commit/b5f4c5ad1469a9de337ee48a1a431ebf7f4709a9))

### Features

- Add regress check, a model-free mutation score gate for CI
  ([`dc3793b`](https://github.com/Dino-Kupinic/regress/commit/dc3793b2baca26e8674031f25465909aec0035c0))


## v0.2.0 (2026-10-04)

### Continuous Integration

- Release automatically from conventional commits
  ([`1f9d755`](https://github.com/Dino-Kupinic/regress/commit/1f9d7556283381a03a4fd5254481b8a72d39c63c))

### Documentation

- Explain multi-file runs and --changed
  ([`ee4519c`](https://github.com/Dino-Kupinic/regress/commit/ee4519c5652a6b34b3b3d836e0169b8f3c9a12bf))

### Features

- Run several source files at once and mutate only changed lines
  ([`b7a678b`](https://github.com/Dino-Kupinic/regress/commit/b7a678bce5c8be186165de2573bae42a3d17c3e5))

### Refactoring

- Share source file discovery between the CLI and the API
  ([`a8319e0`](https://github.com/Dino-Kupinic/regress/commit/a8319e07c4d3d774ecb6087fc0b27fcb79327ef2))


## v0.1.0 (2026-09-29)

- Initial Release
