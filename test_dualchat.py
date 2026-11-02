import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import dualchat


class Tests(unittest.TestCase):
    def test_parse_irc(self):
        line = "@display-name=Bob;color= :bob!bob@bob.tmi.twitch.tv PRIVMSG #me :hi :) there"
        self.assertEqual(dualchat.parse_irc(line), ({"display-name": "Bob", "color": ""}, "bob", "PRIVMSG", "hi :) there"))
        self.assertEqual(dualchat.parse_irc(":tmi.twitch.tv 001 me :Welcome")[2], "001")
        sub = r"@msg-id=resub;system-msg=Bob\ssubscribed\sfor\s3\smonths\:\sTier\s1 :tmi.twitch.tv USERNOTICE #me :love u"
        tags, _, cmd, text = dualchat.parse_irc(sub)
        self.assertEqual((cmd, tags["msg-id"], text), ("USERNOTICE", "resub", "love u"))
        self.assertEqual(tags["system-msg"], "Bob subscribed for 3 months; Tier 1")
        self.assertEqual(dualchat.parse_irc("@msg-id=sub :tmi.twitch.tv USERNOTICE #me")[3], "")

    def test_json_stream_sse_split_across_chunks(self):
        body = 'data: {"items": [{"t": "héllo"}], "nextPageToken": "a"}\n\n: keepalive\n\ndata: {"nextPageToken": "b"}\n\n'
        raw = body.encode()
        chunks = [raw[i:i + 7] for i in range(0, len(raw), 7)]  # cuts through the 2-byte "é" too
        self.assertEqual(list(dualchat.json_stream(chunks)),
                         [{"items": [{"t": "héllo"}], "nextPageToken": "a"}, {"nextPageToken": "b"}])

    def test_json_stream_json_array(self):
        chunks = [b'[{"a": 1}\n', b',\r\n{"b": ', b'"}"}\n]']
        self.assertEqual(list(dualchat.json_stream(chunks)), [{"a": 1}, {"b": "}"}])

    def test_error_body_unwraps_list(self):
        def err(body):
            return mock.Mock(read=lambda: body)
        self.assertEqual(dualchat.error_body(err(b'[{"error": {"message": "bad"}}]')), {"error": {"message": "bad"}})
        self.assertEqual(dualchat.error_body(err(b'{"error": "x"}')), {"error": "x"})
        self.assertEqual(dualchat.error_body(err(b"not json")), {})

    # made-up keys, assembled so no key-shaped string sits in the source
    GID = "123456789012-" + "a1" * 16 + ".apps.googleusercontent.com"
    SECRET = "GOCSPX-" + "Xy_9" * 7
    TWITCH = "abc123" * 5

    def test_capture_google_needs_exactly_one_id_and_secret(self):
        page = f"OAuth client created\nClient ID\n{self.GID}\nClient secret\n{self.SECRET}\nOK"
        self.assertEqual(dualchat.capture_google(page), (self.GID, self.SECRET))
        self.assertIsNone(dualchat.capture_google(f"Clients list {self.GID}"))  # no secret on screen
        other = "987654321098-" + "b2" * 16 + ".apps.googleusercontent.com"
        self.assertIsNone(dualchat.capture_google(f"{page} {other}"))  # two clients: ambiguous

    def test_capture_twitch_from_manage_url(self):
        self.assertEqual(dualchat.capture_twitch(f"https://dev.twitch.tv/console/apps/{self.TWITCH}"), self.TWITCH)
        self.assertEqual(dualchat.capture_twitch(f"https://dev.twitch.tv/console/apps/{self.TWITCH}?x=1"), self.TWITCH)
        self.assertIsNone(dualchat.capture_twitch("https://dev.twitch.tv/console/apps/create"))
        self.assertIsNone(dualchat.capture_twitch(f"https://evil.example/console/apps/{self.TWITCH}"))

    def test_check_keys(self):
        with mock.patch.dict(dualchat.CFG, {}, clear=True):
            ok, err = dualchat.check_keys("youtube", {"client_id": f" {self.GID} ", "client_secret": self.SECRET})
            self.assertEqual((ok, err), ({"client_id": self.GID, "client_secret": self.SECRET}, None))
            self.assertIsNotNone(dualchat.check_keys("youtube", {"client_id": "nope", "client_secret": self.SECRET})[1])
            self.assertIsNotNone(dualchat.check_keys("youtube", {"client_id": self.GID, "client_secret": ""})[1])
            ok, err = dualchat.check_keys("twitch", {"client_id": self.TWITCH, "channel": "#SomeChannel"})
            self.assertEqual(ok, {"client_id": self.TWITCH, "channel": "somechannel"})
            self.assertIsNotNone(dualchat.check_keys("twitch", {"client_id": self.TWITCH, "channel": "a b"})[1])
        with mock.patch.dict(dualchat.CFG, {"youtube": {"client_secret": self.SECRET}}, clear=True):
            ok, _ = dualchat.check_keys("youtube", {"client_id": self.GID, "client_secret": ""})  # keeps saved secret
            self.assertEqual(ok["client_secret"], self.SECRET)

    def test_placeholders_are_not_ready(self):
        example = json.loads((Path(dualchat.__file__).parent / "config.example.json").read_text())
        with mock.patch.dict(dualchat.CFG, example, clear=True):
            self.assertFalse(dualchat.is_ready("youtube") or dualchat.is_ready("twitch"))
            self.assertEqual(dualchat.key("twitch", "client_id"), "")
        with mock.patch.dict(dualchat.CFG, {"youtube": {"client_id": self.GID}}, clear=True):
            self.assertFalse(dualchat.is_ready("youtube"))  # YouTube also needs the secret

    def test_api_refuses_when_a_setup_page_is_showing(self):
        app = mock.Mock(at_home=mock.Mock(return_value=False), signins={"youtube": ("u", "c")})
        api = dualchat.Api(app)
        for call in (api.hello, lambda: api.poll(0), lambda: api.send("hi"), api.toggle_theme, api.finish_setup,
                     lambda: api.save_keys("twitch", {}), lambda: api.open_page("google_api"),
                     lambda: api.open_signin("youtube"), lambda: api.window_cmd("close"),
                     lambda: api.begin_resize("top")):
            self.assertIsNone(call())
        self.assertEqual([c for c in app.method_calls if c[0] != "at_home"], [])  # nothing reached the app
        app.at_home.return_value = True
        api.open_page("not_a_page")
        api.window_cmd("rm -rf")
        api.begin_resize("bottom")  # only the top edge is ours; the rest is the native border
        app.browse.assert_not_called()
        app.window_cmd.assert_not_called()
        app.begin_resize.assert_not_called()

    def test_broken_config_is_set_aside_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(dualchat.CFG, {}, clear=True):
            cfg = Path(d) / "config.json"
            cfg.write_text("{ broken")
            with mock.patch.object(dualchat, "CONFIG", cfg):
                note = dualchat.load_config()
                self.assertIn("renamed", note)
                kept = list(Path(d).glob("config.broken-*.json"))
                self.assertEqual(len(kept), 1)
                self.assertEqual(kept[0].read_text(), "{ broken")
                dualchat.CFG["theme"] = "light"
                dualchat.save_config()
                self.assertEqual(json.loads(cfg.read_text()), {"theme": "light"})

    def test_clean_blocks_irc_injection(self):
        self.assertEqual(dualchat.clean("hi\r\nPRIVMSG #x :spam  "), "hi PRIVMSG #x :spam")

    def test_get_token_refreshes_once_and_keeps_refresh_token(self):
        with tempfile.TemporaryDirectory() as d:
            tokens = Path(d) / "tokens.json"
            tokens.write_text(json.dumps({"youtube": {"access_token": "old", "refresh_token": "r1", "expires_at": 0}}))
            with mock.patch.object(dualchat, "TOKENS", tokens), \
                 mock.patch.dict(dualchat.CFG, {"youtube": {"client_id": "id", "client_secret": "s"}}), \
                 mock.patch.object(dualchat, "http", return_value={"access_token": "new", "expires_in": 3600}) as h:
                self.assertEqual(dualchat.get_token("youtube", print), "new")
                self.assertEqual(dualchat.get_token("youtube", print), "new")  # cached now
            h.assert_called_once()
            self.assertEqual(json.loads(tokens.read_text())["youtube"]["refresh_token"], "r1")


if __name__ == "__main__":
    unittest.main()
