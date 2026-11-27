# Oximut

Oximut mutation-tests JavaScript and TypeScript with Vitest.

This package contains the prebuilt Oximut CLI, Vitest adapter, native zygote
addon, and locked dependencies from the validated Ubuntu 24.04 x86-64 prefix.
Ubuntu 24.04 x86-64 with glibc is the supported platform. Use the Node version
declared in this package's `engines` field and Python 3.11 or newer on PATH.

```sh
npm install --save-dev oximut
npx oximut --help
```

Install and configure Vitest in the project you want to mutation-test. See
[Oximut's installation guide](https://github.com/RuiCostafrg/oximut/blob/main/docs/install.md)
for project setup and CLI usage.
