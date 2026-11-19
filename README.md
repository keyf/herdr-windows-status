# Herdr Windows Status for Hermes

A small Windows-only [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that reports interactive CLI lifecycle state to [Herdr](https://herdr.dev/) using the OSC 7501 Program Status Protocol.

Herdr can then show a Hermes terminal as an agent rather than as an ordinary shell: working, waiting for input, finished, failed, or gone.

## What it reports

- `idle` when the interactive client starts or an interrupted turn settles;
- `working` after submitting a turn and after a blocking prompt is answered;
- `blocked` with a coarse kind for command approval (`permission`), clarification (`question`), and sign-in/secret input (`auth`);
- `done` after a successful turn;
- `error` after a failed turn or an uncaught client exception;
- `clear` when the interactive client unloads, so Herdr does not retain a stale agent record.

The plugin writes directly to `CONOUT$`. This matters on Windows because the Hermes gateway is not the terminal process that Herdr observes.

## Compatibility tested

| Component | Verified version |
| --- | --- |
| Windows | Windows 11 |
| Hermes Agent | `0.23.1` (`stable`, commit `3b4215785882`) |
| Herdr Core CLI (`hc`) | `0.1.21` |
| Plugin | `0.2.1` |

The bridge uses private Hermes CLI seams (`GatewayAgent` and `CLIModalMixin`). Run the verification suite after a Hermes upgrade.

## Install

Install from GitHub into the Hermes profile that runs inside a Herdr terminal:

```sh
hermes plugins install arseniy/herdr-windows-status --enable
```

Close and reopen the interactive Hermes CLI tab after installation. The gateway itself does not need a restart.

For a local checkout, copy this directory into `%HERMES_HOME%\plugins\herdr-windows-status`, add `herdr-windows-status` to `plugins.enabled`, then open a new Hermes CLI tab.

## Verify

```sh
python test_herdr_desktop_status.py
python -m py_compile __init__.py
hermes plugins doctor herdr-windows-status --ci
```

The tests are hermetic: they do not send an OSC sequence to a real terminal and do not make any model request. A manual acceptance check is still required after installation: submit a harmless prompt in a Herdr-hosted Hermes tab and observe `working`, `done`, and `clear` after the tab closes.

## Security and privacy

The plugin exports only lifecycle state and a coarse blocked kind. It does not export commands, prompts, model replies, error text, paths, URLs, or credentials. Herdr's terminal status channel is observable UI and must not be treated as a secret transport.

## Limitations and troubleshooting

Only Windows interactive Hermes CLI sessions hosted in a Herdr-observed console are supported. Diagnostic output, if needed, is in `debug.log` beside the plugin; it is ignored by Git. Disable the plugin in the Hermes profile and reopen the CLI tab to stop status publishing.

## Acknowledgements

This project was inspired by [christopher-s/herdr-osc7501](https://github.com/christopher-s/herdr-osc7501), a Hermes/Herdr OSC 7501 integration. It informed the client-terminal ownership model and lifecycle vocabulary. This is an independent Windows-specific implementation using `CONOUT$`; consult the upstream project's license before reusing its code.

## License

MIT. See [LICENSE](LICENSE).
