from fuzz import BaseHook, GenOutcome


class ItemLocationMismatchError(Exception):
    def __init__(self, item_count, location_count, details):
        self.item_count = item_count
        self.location_count = location_count
        self.details = details
        super().__init__(
            f"Item count ({item_count}) != Location count ({location_count}). {details}"
        )

    def __reduce__(self):
        return (self.__class__, (self.item_count, self.location_count, self.details))


class Hook(BaseHook):
    def setup_worker(self, args):
        import Fill
        import Main

        def check_item_location_count(multiworld):
            item_count = len(multiworld.itempool)
            location_count = len(multiworld.get_unfilled_locations())

            if item_count != location_count:
                game_counts = {}
                for player, world in multiworld.worlds.items():
                    if player == 0:
                        continue
                    game_name = world.game
                    player_items = sum(1 for item in multiworld.itempool if item.player == player)
                    player_locations = sum(
                        1 for loc in multiworld.get_unfilled_locations() if loc.player == player
                    )
                    if game_name not in game_counts:
                        game_counts[game_name] = {"items": 0, "locations": 0, "players": []}
                    game_counts[game_name]["items"] += player_items
                    game_counts[game_name]["locations"] += player_locations
                    game_counts[game_name]["players"].append(player)

                details_parts = []
                for game, counts in game_counts.items():
                    diff = counts["items"] - counts["locations"]
                    if diff != 0:
                        sign = "+" if diff > 0 else ""
                        details_parts.append(
                            f"{game}: {counts['items']} items, {counts['locations']} locations ({sign}{diff})"
                        )

                details = "; ".join(details_parts) if details_parts else "Unknown cause"
                raise ItemLocationMismatchError(item_count, location_count, details)

        original_distribute = Fill.distribute_items_restrictive

        def checked_distribute(multiworld, *args, **kwargs):
            check_item_location_count(multiworld)
            return original_distribute(multiworld, *args, **kwargs)

        Fill.distribute_items_restrictive = checked_distribute
        Main.distribute_items_restrictive = checked_distribute

    def reclassify_outcome(self, outcome, raised):
        if outcome == GenOutcome.Failure:
            if isinstance(raised, ItemLocationMismatchError):
                return GenOutcome.Failure, raised

        return GenOutcome.Success, None
