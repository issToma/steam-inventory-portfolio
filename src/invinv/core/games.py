"""Games whose items trade on the Steam Community Market.

An item belongs to a game through its Steam app id. A user's inventory for a game lives in
one "context" (2 for most games, 6 for Steam's own trading cards, backgrounds and emoticons).
"""

from dataclasses import dataclass

CS2_APPID = 730
STEAM_APPID = 753


@dataclass(frozen=True)
class Game:
    appid: int
    name: str
    contextid: str = "2"


# Shown before the user's inventory is known, and as a fallback for the names.
KNOWN_GAMES: dict[int, Game] = {
    g.appid: g
    for g in (
        Game(CS2_APPID, "Counter-Strike 2"),
        Game(570, "Dota 2"),
        Game(440, "Team Fortress 2"),
        Game(252490, "Rust"),
        Game(578080, "PUBG: BATTLEGROUNDS"),
        Game(STEAM_APPID, "Steam", contextid="6"),
    )
}


def game_name(appid: int) -> str:
    game = KNOWN_GAMES.get(appid)
    return game.name if game else f"App {appid}"
