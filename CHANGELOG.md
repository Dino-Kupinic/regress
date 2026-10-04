# CHANGELOG

<!-- version list -->

## v0.5.0 (2026-10-04)

### Bug Fixes

- **web**: Give the settings save buttons room in a narrow card
  ([`dc4ee57`](https://github.com/Dino-Kupinic/regress/commit/dc4ee579edf1c00161f426611be6ae7a9d4cf686))

### Documentation

- Describe multi-file runs in the web app and the HTTP API
  ([`c49aca1`](https://github.com/Dino-Kupinic/regress/commit/c49aca146d59cef5da7aab47679d3deb8a8e196d))

### Features

- **api**: Run several source files over HTTP as one batch
  ([`9c6c47b`](https://github.com/Dino-Kupinic/regress/commit/9c6c47beb3d9390d6cfba4078c4dccaf9b7eb246))

- **web**: Select several source files for a new run
  ([`1a078b3`](https://github.com/Dino-Kupinic/regress/commit/1a078b394ee6c8aa92da9d07207f05f192d6455f))


## v0.4.0 (2026-10-04)

### Documentation

- Explain how to configure each model provider
  ([`0dc04af`](https://github.com/Dino-Kupinic/regress/commit/0dc04af70aee80d22470df0e77cb784f98c9ed16))

### Features

- Run on Anthropic models and OpenAI-compatible servers
  ([`e9d7862`](https://github.com/Dino-Kupinic/regress/commit/e9d7862662bce81d877c8eaf993ec0fd70eb5da6))

- **web**: Choose the provider in settings and show its key
  ([`b54917c`](https://github.com/Dino-Kupinic/regress/commit/b54917c67de30b94b370248b1df261537b0c73b1))


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
