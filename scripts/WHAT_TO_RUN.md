# What To Run

Use the stupid-simple script names in `scripts\`.

## First time only

If you already ran the first-time setup flow once, skip this.

Run:

```cmd
scripts\one_time_only_DELETE_ME.bat
```

That does:

1. `rotate_keys.bat`
2. `deploy.bat`

## Normal deploy

Run:

```cmd
scripts\deploy.bat
```

Use this for code changes.

## Update docs/settings/search context

Run:

```cmd
scripts\update.bat
```

This is not a code deploy. It refreshes docs/search context and related runtime
inputs.

## Rotate secrets only

Run:

```cmd
scripts\rotate_keys.bat
```

## Refresh docs/search context

Run:

```cmd
scripts\refresh_context.bat
```

`update.bat` and `refresh_context.bat` now mean the same thing.

If you do not already have a search-context store, use:

```cmd
scripts\refresh_context.bat --create
```

## Print the admin/API token

Run:

```cmd
scripts\get_token.bat
```
