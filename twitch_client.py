from twitchAPI import Twitch


class TwitchClient:
	def __init__(self, client_id: str, client_secret: str):
		self.client = Twitch(client_id, client_secret)

	def get_streamer_info_from_name(self, name: str):
		return self.client.get_users(logins=[name.lower()]).get("data", [])[0]

	def get_streamer_info_from_id(self, streamer_id: int):
		return self.client.get_users([str(streamer_id)]).get("data", [])[0]

	def get_display_name(self, streamer_id: int):
		return self.get_streamer_info_from_id(streamer_id).get("display_name", "<failed to get display name>")

	def get_login_name(self, streamer_id: int):
		streamer_info = self.get_streamer_info_from_id(streamer_id)

		if "login" not in streamer_info:
			raise ValueError(f"Failed to get streamer login name for streamer with id '{streamer_id}'.")

		return streamer_info["login"]

	def get_stream_title(self, streamer_id: int):
		result = self.client.get_channel_information(str(streamer_id)).get("data", [])[0]
		return result["title"]

	def get_stream_status_by_streamer_id(self, streamer_id: int):
		return self.get_stream_info(streamer_id)["is_live"]

	def is_live(self, streamer_id: int):
		result = self.get_stream_info(streamer_id)
		return result is not None

	def get_stream_info(self, streamer_id: int):
		result = self.client.get_streams(user_id=[str(streamer_id)]).get("data", [])
		if len(result) == 0:
			return None

		return result[0]
