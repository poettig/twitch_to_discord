import json
import typing

import requests


class NightbotClient:
	BASE_URL = "https://api.nightbot.tv/1"
	CHANNELS_URL = f"{BASE_URL}/channels/t/{{}}"
	COMMANDS_URL = f"{BASE_URL}/commands"

	def __init__(self):
		self.session = requests.Session()

	def get_json(self, url: str, headers=None) -> typing.Dict:
		try:
			response = self.session.get(url, headers=headers)
		except requests.RequestException as e:
			raise ValueError(f"Failed request to '{url}': {e}")

		if not 200 <= response.status_code < 300:
			raise ValueError(
				f"Non-Success return code for request to '{url}': "
				f"{response.status_code} - {response.content.decode(response.encoding)}"
			)

		try:
			json_data = response.json()
		except json.JSONDecodeError as e:
			raise ValueError(f"Failed to decode JSON response from Nightbot: {e}")

		return json_data

	def get_commands_by_channel_name(self, channel_name: str) -> typing.List[typing.Dict[str, typing.Union[str, int]]]:
		channel_id = self.get_channel_id_by_name(channel_name)
		return self.get_commands_by_channel_id(channel_id)

	def get_commands_by_channel_id(self, channel_id: str) -> typing.List[typing.Dict[str, typing.Union[str, int]]]:
		commands_data = self.get_json(NightbotClient.COMMANDS_URL, headers={"Nightbot-Channel": channel_id})

		if "commands" not in commands_data:
			raise ValueError("Commands missing in Nightbot response.")

		return commands_data["commands"]

	def get_channel_id_by_name(self, channel_name: str) -> str:
		channel_data = self.get_json(NightbotClient.CHANNELS_URL.format(channel_name.lower()))

		if "channel" not in channel_data or "_id" not in channel_data["channel"]:
			raise ValueError("Channel ID not found in Nightbot response.")

		return channel_data["channel"]["_id"]
