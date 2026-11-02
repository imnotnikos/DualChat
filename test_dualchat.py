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

    def test_viewer_line(self):
        self.assertEqual(dualchat.viewer_line([("YT", 12), ("TW", 5)]), "Viewers:   YT 12   TW 5   Total 17")
        self.assertEqual(dualchat.viewer_line([("YT", 0), ("TW", None)]), "Viewers:   YT 0   TW offline   Total 0")
        self.assertEqual(dualchat.viewer_line([("TW", None)]), "Viewers:   TW offline")

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

    def test_emoji_sequences_match_whole(self):
        text = "gg 😂 👍🏽 👨‍👩‍👧 🇺🇸 ❤️‍🔥 1️⃣ ❤ ★ ☀ done"
        self.assertEqual(dualchat.EMOJI_RE.findall(text), ["😂", "👍🏽", "👨‍👩‍👧", "🇺🇸", "❤️‍🔥", "1️⃣", "❤"])
        self.assertEqual(dualchat.EMOJI_RE.findall("plain 123 text #1"), [])  # digits alone aren't keycaps

    def test_twemoji_name(self):
        self.assertEqual(dualchat.twemoji_name("❤️"), "2764")  # FE0F dropped outside ZWJ sequences
        self.assertEqual(dualchat.twemoji_name("🏳️‍🌈"), "1f3f3-fe0f-200d-1f308")  # kept inside them
        self.assertEqual(dualchat.twemoji_name("👍🏽"), "1f44d-1f3fd")

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
