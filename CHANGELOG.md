# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

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
