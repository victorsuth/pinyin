# 拼音大冒险（Pinyin Adventure）

A Duolingo-style pinyin practice game for a 2nd-grade child in mainland China who
still mixes up parts of Hanyu Pinyin. Single self-contained `index.html`
(HTML + CSS + JS, no build, no dependencies, works offline by double-click).

The user (Victor, parent, IT admin) communicates in Chinese — reply in Chinese.
All UI copy is Simplified Chinese, written for a ~7-year-old.

## Deployment
- Hosted on **Netlify** (static, publish dir `/`, no build command).
- Plan: GitHub repo → Netlify auto-deploy on push to `main`. Repo was just
  created by the user; ask for `owner/repo` if not already set as the remote.
- Keep the file named `index.html` at the repo root.
- Progress is stored in `localStorage` (keys prefixed `pyadv_`), so it is
  per-browser and tied to the site URL — don't change the domain casually, and
  never rename/remove existing storage keys (the child would lose progress).

## How the game works
- 7 categories + 混合挑战 + 错题本复习:
  `nasal` 前后鼻音, `pingqiao` 平舌翘舌, `shape` 形近字母 (b/d/p/q, m/n, f/t),
  `tone` 声调, `umlaut` ü 的规则, `whole` 整体认读, `triple` 三拼音节.
- Item format in `CATS[id].items`: `[汉字, 词语, 数字标调拼音, [易混干扰项]]`,
  e.g. `['山','山羊','shan1',['shang1','san1']]`. `v` = ü. `py()` converts
  `shan1` → `shān` using standard tone-mark placement rules.
- `EMO` maps 汉字 → emoji picture hint. Characters without an emoji are only
  used in 看字选拼音 questions in hint mode.
- Question types: `c2p` 看字选拼音, `listen` 听音选拼音 (disabled if no zh voice),
  `p2c` 看拼音选字 (in hint mode becomes 看拼音选图).
- 10 questions per lesson; wrong answers are re-queued at the end and added to
  the 错题本; stars by first-try accuracy (≥90% ★★★, ≥70% ★★).
- Gamification: XP, combo streak, daily goal (3 lessons), day streak, confetti.
- Settings on home screen: 模式 (看图+读字 default / 挑战不提示), 语速 (很慢 .45 default /
  慢 .6 / 正常 .8), 🔧 声音检测 diagnostics panel.

## Important lessons learned (don't regress these)
1. **The child can't read many characters yet.** That's why hint mode exists:
   picture + auto-read for every prompt. Don't add questions that require
   reading unfamiliar characters in hint mode.
2. **Speech must start synchronously inside a tap handler** (iOS requirement).
   No `setTimeout` before `speechSynthesis.speak()`. The 3 parts of a reading
   (字 / 词 / 词的字) are queued at once.
3. **Only call `speechSynthesis.cancel()` when `speaking || pending`.** On iOS,
   cancel-then-speak when idle silences the next utterance.
4. Always stop speech on next question / quit / result, or old readings keep
   playing over the new question.
5. **iPhone silent switch mutes Web Speech** even though music apps play.
   `navigator.audioSession.type='playback'` + a looping silent `<audio>` were
   added (helps Web Audio beeps) but speech is still muted in silent mode —
   user accepted this. A yellow tip (`MUTE_TIP`) tells iOS users to flip the
   switch. The real fix, if ever wanted: pre-recorded audio files played via
   `<audio>` (e.g. generated with macOS `say -v Tingting` or edge-tts on the
   user's machine; the cloud sandbox couldn't reach TTS services).
6. `confirm()`/`alert()` are avoided (blocked in some embedded viewers); quit
   confirmation is an in-page sheet (`askQuit`).
7. In the WeChat in-app browser speech usually doesn't work — diagnostics panel
   tells the user to open in Safari.

## Possible next steps the user might ask for
- Add more items to whichever category the 错题本 shows is weakest.
- Pre-recorded audio version (works in silent mode and WeChat).
- Remove the silent-audio keepalive if it annoys (it pauses background music).

## Testing
No test suite. After edits: extract the `<script>` and run `node --check`, and
for logic changes run `makeQ()` over all items in both `hintMode` values with a
stubbed DOM/localStorage to confirm every question gets 4 distinct options.
