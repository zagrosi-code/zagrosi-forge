import unittest
from settings import ConfigReader, load_config, settings_text
from service import bind_address


class SettingsTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(load_config([]), {
            "host": "127.0.0.1", "port": 8000, "debug": False, "tags": []})

    def test_later_layer_and_environment_win(self):
        layers = [("base", {"port": 9000}), ("local", {"port": 9100})]
        self.assertEqual(bind_address(layers, {"APP_HOST": "localhost"}), "localhost:9100")

    def test_lists_replace_and_none_resets(self):
        layers = [("base", {"tags": ["a"], "port": 9000}),
                  ("local", {"tags": ["b"], "port": None})]
        self.assertEqual(load_config(layers)["tags"], ["b"])
        self.assertEqual(load_config(layers)["port"], 8000)

    def test_result_is_detached(self):
        data = {"tags": ["a"]}
        result = load_config([("local", data)])
        result["tags"].append("b")
        self.assertEqual(data, {"tags": ["a"]})

    def test_reader_and_text(self):
        reader = ConfigReader([], {"APP_DEBUG": "true"})
        self.assertTrue(reader.snapshot()["debug"])
        self.assertEqual(reader.render(), settings_text([], {"APP_DEBUG": "true"}))

    def test_unknown_setting(self):
        with self.assertRaisesRegex(ValueError, "^Unknown setting: other$"):
            load_config([("local", {"other": 1})])
