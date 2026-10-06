# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Tech trees** (`科技树`): every nation's tree for all five vehicle classes,
  drawn on the wiki's own grid - rank by rank, five columns per row, columns
  continuing into the next rank, the researchable half on the left and the
  premium half on the right.  Folders such as `M4A1/M4/M4A2` open up so each
  member is separately selectable, because they are researched separately.
- **Research planning**: click any vehicle to get the minimum research points
  and silver lions to reach it, plus the exact research order.  It walks the
  prerequisite chain (through folders), adds the vehicles needed to satisfy each
  rank gate, and lists every vehicle it counted with the reason, so the total can
  be checked against the game.  The rank-unlock numbers are the game's own:
  ground 4/5/6/6/5/5/5, aviation 3/6/6/6/5/5/5/3 including the rank IX gate, and
  for navies everything in the previous rank capped at six.  Helicopter
  trees open at rank V and are opened by **a rank V ground or air vehicle of
  the same nation**, not by a lower helicopter rank; every later rank then
  needs one helicopter of the rank below.  The cheapest such entry route is
  computed and reported separately from the helicopters themselves, so a
  player who already owns a rank V vehicle can subtract it.
- **Penetration comparison** (`穿深对照`): pick any vehicle's any shell, a
  distance and an impact angle, and see the verdict for every armour plate of any
  target - thickness, geometric effective thickness, published penetration,
  verdict and margin - together with the reverse direction.  Shell lists carry
  Chinese type names and their weapon; ship armour rows are read from their own
  labels instead of being forced into the tank layout.
- A rank-IX gate for aircraft.  The wiki has had rank IX jets for a while; the
  tree parser's roman-numeral table stopped at VIII, so those twenty-two aircraft
  were being filed under rank VIII.

### Notes

- The penetration panel is a **comparison of published numbers, not a
  simulation** of the game's ballistics.  War Thunder's slope effects,
  normalisation and ricochet rules are not published, so angled results say
  outright that they are optimistic rather than inventing a formula.  No game
  models are extracted or redistributed.
- Cross-rank prerequisites ("the last vehicle of a column unlocks the first of the
  next rank") are not written into the wiki's markup, so they are reconstructed
  from the column position and labelled as reconstructed in the UI.
- Research costs come from the wiki's vehicle pages.  Silver lions are totalled
  as "buy everything along the way"; if the game only requires researching them,
  the real figure is lower.

## [1.1.0] - 2026-10-05

First public release.

### Added

- **Two independent game clients.** The live and DEV-server installs are
  detected, stored and switched separately, so pointing the tool at one never
  disturbs the other. DEV detection follows the official markers
  (`curCircuit="dev"`, an empty `matchingdevmode` file, or `wt_dev_launcher.exe`).
  When no DEV client is found the page says so plainly instead of borrowing the
  live path.
- **Vehicle strength comparison** over 3,251 vehicles (10 nations, 5 classes):
  a searchable picker, a radar chart scaled to the stronger value of the pair,
  a per-metric table with the raw figures and the source, and a full dump of
  both vehicles' wiki parameters.
- **Statistics page**, gated behind an explicit disclaimer, with local,
  genuinely countable figures (play time, replays, screenshots, client version).
- **Sound mod install with a complete rollback set**, verified by restoring and
  comparing every file byte for byte against the pre-install state.
- **`--selftest`**, which imports all 35 modules, runs five smoke checks and
  writes a report to `%APPDATA%\WTToolbox\logs\selftest.txt`.
- Rounded, shadowed frameless window that squares off when maximised.
- Light and dark themes that switch in place without a restart.

### Changed

- Renamed from ThunderKit to WTToolbox throughout: executable, window title,
  data directory, Python package and brand assets. The old
  `%APPDATA%\ThunderKit` directory is migrated automatically on first launch,
  and the migration is idempotent and never overwrites newer files.
- The vehicle index is now built from the official `sitemap-units.xml` instead
  of the tech-tree links, and is case-insensitive to look up.
- Network requests to the wiki time out after 15 s and retry once, instead of
  hanging for 30 s.

### Fixed

- **A sound mod could not be installed after setting the game directory** - the
  page had no `on_install_changed` hook, so it stayed unmounted forever.
- **Switching the theme froze the window** - the rebuild deleted the widgets but
  not the layout, and `deleteLater()` left them alive long enough for
  `setStyleSheet` to re-polish all 1,042 of them (2.9 s; 0.03 s once they are
  really gone).
- **The theme could go dark but never back to light** - `set_theme` compared
  against a palette that was only ever assigned once. The old test called the
  handler directly and so never exercised the button path.
- **Icons were blurry** - `setDevicePixelRatio()` was called before painting,
  which scaled the painter's coordinate system so only the SVG's top-left
  quarter was drawn.
- **A tank's machine-gun rate was reported as its gun's.** The wiki keeps the
  main gun and the machine guns in separate `game-unit_weapon` blocks; the
  parser flattened them. A T-34 claimed "600 shots/min". Metrics now come from
  the primary weapon only, and its rate is derived from the reload time
  (6.9 s -> 8.7 rounds/min). Machine-gun data is still shown, labelled.
- **"Thrust-to-weight" was the wrong label** for the hp/t figure on tanks; it is
  power-to-weight. Hover explanations were added for the metrics that are easy
  to confuse.
- **Selecting an aircraft or a ship then pressing compare appeared to do
  nothing.** 398 `*_group` folder nodes from the tech tree had been indexed;
  none of them has a wiki page, so 14 % of the aircraft entries were dead links
  that could only ever 404. They are gone, and the 16 vehicles the tech tree
  dropped but the wiki still documents (Leopard I, Panther II, the UCAVs, the
  floatplanes) are now included.
- **A tank could be compared against a battleship** and produced a chart. Cross
  service comparisons are now refused up front with a clear reason, and the
  second picker locks to the first vehicle's class.
- The status of a fetch is now drawn in the chart area, so a slow wiki never
  looks like a frozen window.
- 28 further defects fixed during development; they are listed in the README's
  verification section.

### Known limitations

See the "Known limitations" section of the README. In short: `.clog` files use
a private compression format and cannot be decoded; replays carry no result
data; the official stats API is behind a 403 anti-bot page, so the app opens the
real page in a browser rather than scraping it.

[Unreleased]: https://github.com/hehehegood6723/WTToolbox/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/hehehegood6723/WTToolbox/releases/tag/v1.1.0
