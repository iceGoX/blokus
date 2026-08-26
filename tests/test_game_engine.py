import unittest

from server.game_engine import (
    BOARD_SIZES,
    PIECES,
    create_game,
    finish_player,
    place_piece,
    resign_player,
    score,
    transform,
)


def room_players(count):
    return [{"id": f"p{index}", "name": f"玩家{index + 1}"} for index in range(count)]


class GameEngineTests(unittest.TestCase):
    def test_piece_inventory_has_21_pieces_and_89_squares(self):
        self.assertEqual(len(PIECES), 21)
        self.assertEqual(sum(len(piece["cells"]) for piece in PIECES), 89)

    def test_two_players_use_opposite_corners(self):
        game = create_game(room_players(2), 2)
        self.assertEqual([player["color"] for player in game["players"]], ["blue", "red"])
        self.assertEqual(game["boardSize"], 14)
        self.assertEqual([player["corner"] for player in game["players"]], [[0, 0], [13, 13]])

    def test_board_sizes_keep_similar_space_pressure(self):
        self.assertEqual(BOARD_SIZES, {2: 14, 3: 17, 4: 20})
        for players, board_size in BOARD_SIZES.items():
            fill_ratio = players * 89 / (board_size * board_size)
            self.assertGreater(fill_ratio, 0.88)
            self.assertLess(fill_ratio, 0.94)

    def test_first_moves_snap_to_each_players_corner(self):
        game = create_game(room_players(2), 2)
        ok, _ = place_piece(game, "p0", "I5", 0, False, 0, 0)
        self.assertTrue(ok)
        ok, _ = place_piece(game, "p1", "I5", 0, False, 13, 13)
        self.assertTrue(ok)
        self.assertEqual(game["board"][0:5], ["blue"] * 5)
        self.assertEqual(game["board"][-5:], ["red"] * 5)

    def test_server_rules_reject_edge_contact_and_accept_corner_contact(self):
        game = create_game(room_players(2), 2)
        self.assertTrue(place_piece(game, "p0", "I5", 0, False, 0, 0)[0])
        self.assertTrue(place_piece(game, "p1", "I5", 0, False, 13, 13)[0])

        ok, message = place_piece(game, "p0", "I1", 0, False, 0, 1)
        self.assertFalse(ok)
        self.assertIn("边贴边", message)

        ok, _ = place_piece(game, "p0", "I1", 0, False, 5, 1)
        self.assertTrue(ok)

    def test_player_cannot_pass_when_a_move_exists(self):
        game = create_game(room_players(2), 2)
        ok, message = finish_player(game, "p0")
        self.assertFalse(ok)
        self.assertIn("仍有合法落点", message)

    def test_player_can_resign_even_when_moves_exist(self):
        game = create_game(room_players(2), 2)
        ok, message = resign_player(game, "p0")
        self.assertTrue(ok)
        self.assertIn("认输", message)
        self.assertTrue(game["players"][0]["out"])
        self.assertEqual(game["players"][game["currentPlayer"]]["id"], "p1")

    def test_finished_player_is_skipped_until_everyone_finishes(self):
        game = create_game(room_players(2), 2)
        game["players"][0]["remaining"] = []
        self.assertTrue(finish_player(game, "p0")[0])
        self.assertEqual(game["status"], "playing")
        self.assertEqual(game["players"][game["currentPlayer"]]["id"], "p1")
        game["players"][1]["remaining"] = []
        self.assertTrue(finish_player(game, "p1")[0])
        self.assertEqual(game["status"], "finished")

    def test_non_current_player_cannot_move(self):
        game = create_game(room_players(3), 3)
        ok, message = place_piece(game, "p1", "I1", 0, False, 19, 0)
        self.assertFalse(ok)
        self.assertIn("还没有轮到你", message)

    def test_transform_normalizes_rotation_and_flip(self):
        cells = [[0, 0], [0, 1], [1, 1]]
        transformed = transform(cells, 1, True)
        self.assertEqual(min(x for x, _ in transformed), 0)
        self.assertEqual(min(y for _, y in transformed), 0)
        self.assertEqual(len({tuple(cell) for cell in transformed}), 3)

    def test_initial_score_is_negative_89(self):
        game = create_game(room_players(4), 4)
        self.assertEqual(score(game["players"][0]), -89)


if __name__ == "__main__":
    unittest.main()
