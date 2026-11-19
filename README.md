# moct

**Turn an idea into a clear brief that another model can execute.** moct helps your chosen authoring model develop a creative concept, translate it into a self-contained prompt with acceptance checks, and recommend a suitable executor. It pauses for you to switch models, keeping authoring and implementation costs under your control. Works with **Codex, DeepSeek Harness, and Kimi Code**.

[Русская инструкция](README.ru.md)

## How it works

1. Select your authoring model and ask moct to create a prompt. Include the outcome, audience, constraints, and relevant materials.
2. The same model develops the concept and immediately writes a self-contained implementation prompt, including acceptance checks.
3. It recommends an execution model based on the available catalog, required tools, task complexity, and verifiable pricing. The author finishes its response here.
4. Switch the entire chat to the recommended model and say **“continue”**. To work in another supported app, paste the complete task block into a new chat and attach the listed materials.
5. The executor performs the task and checks the result against the acceptance criteria.

The authoring model consumes its own allowance. Implementation and verification consume the allowance of the model selected for those steps. Check the chat's model selector before continuing. If the recommended model is already selected, explicitly say **“continue in the current model”**.

## Install

Clone this repository and open its directory in your terminal.

```bash
git clone https://github.com/smakdenis/moct.git
cd moct
```

The plugin, marketplace, and skill all use the name `moct`. Codex invokes the namespaced skill as `$moct:moct`, DeepSeek Harness uses `/moct`, and Kimi Code uses `/skill:moct`.

### Codex

```bash
codex plugin marketplace add "$PWD" --json
codex plugin add moct@moct --json
```

Review and trust both moct hooks when Codex prompts you. Start a new chat, then write:

```text
$moct:moct Create a prompt for …
```

Natural-language requests such as “Create a prompt for …” can also activate the skill. The bundled `install.command` provides a macOS installer.

### DeepSeek Harness

Requires Python 3 and a Harness profile with filesystem skills enabled.

```bash
python3 install_skill.py deepseek
```

Start a new session and write:

```text
/moct Create a prompt for …
```

### Kimi Code

```bash
python3 install_skill.py kimi
```

Start an interactive session and write:

```text
/skill:moct Create a prompt for …
```

Use `/model` to change the session's model. With the installed Kimi Code version used in testing, explicit skill activation was verified through the interactive ACP path. A `kimi -p` probe passed the literal command without expanding the skill.

The installer respects `DSH_HOME` and `KIMI_CODE_HOME`. Default locations are `~/.dsh/skills/moct` and `~/.kimi-code/skills/moct`. Add `--project /path/to/project` for a project-local installation. An identical installation can be retried. Conflicting content produces an error naming the destination, allowing you to inspect it.

For the regular Kimi chat app, paste the self-contained task block and attach its materials. The native `/skill:moct` command belongs to Kimi Code.

## Learn from completed work

Ask moct to review a mismatch between the prompt and the delivered result. It checks whether the prompt missed a requirement, the executor skipped an existing requirement, or the chosen environment lacked a tool.

To retain a useful check, explicitly ask it to remember a short rule for similar tasks. For example:

> Remember for data tasks: when zero and an unknown value have different meanings, include one acceptance example for each.

Approved rules contain a task scope, a condition, and a check. They live locally in `~/.moct/lessons.sqlite3`. All three environments on the same computer can read this profile when their permissions and Python environment allow it. Ask to list rules or forget a rule by its ID. Transfer a rule as text when moving between devices or to a chat without filesystem access.

The profile stores only the short text explicitly approved for saving. Its contents are treated as user notes subject to the current task's instructions.

## Practical limits

Model selection is manual. In Codex, a trusted hook can block a recognized continuation in the author's model before another model turn begins. The guard depends on a saved marker in the same chat, recognized wording, and a verified model ID. It expires after seven days, and an unrelated message clears it. When writing is unavailable or hooks are skipped, check the selector yourself.

In Harness and Kimi, the pause is an instruction to the author. A request specifically for the prompt first gives the moct predictable handoff. Generation quality and instruction following vary by model. Unknown account pricing is reported as unknown.

The plugin refreshes model evidence when preparing a prompt. Future model releases are considered through the current client's catalog and available official information. Scheduled release monitoring is configured separately in the user's host application.

## Verification

```bash
python3 -B -m unittest discover -s tests -v
bash -n install.command
```

The package has 35 tests covering request recognition, model handoffs, catalog freshness, client installation, concurrent retries, and lesson save/read/delete behavior. Native discovery was checked with Codex, DeepSeek Harness 0.2.2-rc.3, and Kimi Code 2.3.3 on macOS. A live Codex prompt-generation test stopped before implementation. Harness discovery and Kimi's local transport were checked separately from their remote models' output quality. See [verification notes](VERIFICATION.md).

## Upgrade from most

Install `moct@moct`, then disable the former `most@most` plugin in Codex so its hooks run once. Review both new moct hooks when prompted. In Harness and Kimi Code, move the previous `skills/most` bundle outside the skills directory after installing `skills/moct`.

On first use, the default lesson helper copies an existing `~/.most/lessons.sqlite3` profile to `~/.moct/lessons.sqlite3`. IDs are preserved and the old file stays available as a backup. An existing moct profile takes priority.

## Project layout

- `plugins/moct/` contains the Codex plugin and shared prompt instructions.
- `clients/moct/` contains the skill bundle for Harness and Kimi Code.
- `install_skill.py` installs the client bundle into the selected client's directory.
- `tests/` contains automated regression checks.

The native chat interfaces provide the user workflow. [Apps SDK UI](https://github.com/openai/apps-sdk-ui) is available for projects that need a separate ChatGPT app interface.
