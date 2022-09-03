import argparse
import asyncio
import json
import logging
import os.path
import re
import typing

import discord
from twitchAPI.twitch import Twitch


def escape_markdown(text: str):
	return re.sub(r"([_~*>`|])", r"\\\1", text)


class InputError(Exception):
	def __init__(self, log_message, user_message="Sorry, an error has occured. Please contact my programmer."):
		self.user_message = user_message
		self.log_message = log_message
		super.__init__(self.log_message)


class Subscriber:
	def __init__(self, discord_id: int, subscribed_streamers: typing.Union[int, typing.Set[int]]):
		self.discord_id = discord_id

		if isinstance(subscribed_streamers, set):
			self.subscribed_streamers = subscribed_streamers
		else:
			self.subscribed_streamers = set()
			self.subscribed_streamers.add(subscribed_streamers)

	@staticmethod
	def from_dict(subscriber_info: dict):
		return Subscriber(subscriber_info.get("discord_id"), set(subscriber_info.get("subscribed_streamers")))

	def to_dict(self):
		# Create from sratch to prevent mutability problems
		return {
			"discord_id": self.discord_id,
			"subscribed_streamers": list(self.subscribed_streamers)
		}

	def add_subscription(self, streamer_id: int):
		self.subscribed_streamers.add(streamer_id)

	def remove_subscription(self, streamer_id: int):
		try:
			self.subscribed_streamers.remove(streamer_id)
		except KeyError:
			# Already in set, but behave idempotently
			pass


class SubscriptionManager:
	subcribers: typing.Dict[int, Subscriber]

	def __init__(self, subscriptions_file_path: str, subscribers: typing.Dict[int, Subscriber] = None):
		self.subscriptions_file_path = subscriptions_file_path

		if subscribers is None:
			self.subscribers = dict()
		else:
			self.subscribers = subscribers

	@staticmethod
	def load_from_file(subscriptions_file_path: str):
		with open(subscriptions_file_path, "r") as fh:
			data = json.load(fh)

		subscribers = dict()
		for subscriber in data:
			subscribers[subscriber["discord_id"]] = Subscriber.from_dict(subscriber)

		return SubscriptionManager(subscriptions_file_path, subscribers)

	def dump_to_file(self, filename: str):
		subscribers = []
		for subscriber in self.subscribers.values():
			subscribers.append(subscriber.to_dict())

		with open(filename, "w") as fh:
			json.dump(subscribers, fh)

	def add_subscription(self, streamer_id: int, subscriber: discord.User):
		existing_subscriber = self.subscribers.get(subscriber.id)
		if existing_subscriber is not None and streamer_id in existing_subscriber.subscribed_streamers:
			return False

		if existing_subscriber is None:
			self.subscribers[subscriber.id] = Subscriber(subscriber.id, streamer_id)
		else:
			existing_subscriber.add_subscription(streamer_id)

		# Dump file
		self.dump_to_file(self.subscriptions_file_path)

		return True

	def remove_subscription(self, streamer_id: int, subscriber: discord.User):
		if (
			subscriber.id not in self.subscribers
			or streamer_id not in self.subscribers.get(subscriber.id).subscribed_streamers
		):
			return False

		self.subscribers[subscriber.id].remove_subscription(streamer_id)

		# Dump file
		self.dump_to_file(self.subscriptions_file_path)

		return True

	def get_subscriptions(self, subscriber: discord.User):
		data = self.subscribers.get(subscriber.id)
		if data is None:
			return False

		return data.subscribed_streamers


class TwitchClient:
	def __init__(self, client_id: str, client_secret: str):
		self.client = Twitch(client_id, client_secret)

	def get_streamer_info_from_name(self, name: str):
		return self.client.get_users(logins=[name.lower()]).get("data", [])[0]

	def get_streamer_info_from_id(self, streamer_id: int):
		return self.client.get_users([str(streamer_id)]).get("data", [])[0]

	def get_display_name(self, streamer_id: int):
		return self.get_streamer_info_from_id(streamer_id).get("display_name", "<failed to get display name>")

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


class DiscordClientConfig:
	def __init__(self, scan_interval: int, subscriptions_file_path: str):
		if scan_interval is None:
			raise ValueError("The scan interval cannot be undefined.")
		elif not isinstance(scan_interval, int) or scan_interval <= 0:
			raise ValueError("The scan interval must be a positive integer.")

		if not subscriptions_file_path:
			raise ValueError("The subscriptions file path cannot be undefined or an empty string.")

		self.scan_interval = scan_interval
		self.subscriptions_file_path = subscriptions_file_path


class DiscordClient:
	COMMAND_REGEX = re.compile("^!([a-z]+)(?: ([^ ]+))?.*$")

	def __init__(self, discord_bot_token: str, twitch_client: TwitchClient, config: DiscordClientConfig):
		intents = discord.Intents.none()
		intents.guilds = True
		intents.messages = True
		intents.message_content = True
		self.client = discord.Client(intents=intents)
		self.twitch_client = twitch_client
		self.config = config

		if os.path.isfile(self.config.subscriptions_file_path):
			self.subscription_manager = SubscriptionManager.load_from_file(self.config.subscriptions_file_path)
		else:
			self.subscription_manager = SubscriptionManager(self.config.subscriptions_file_path)

		async def twitch_watcher():
			titles = dict()

			while True:
				# Collect set of all subscribed streamers
				subscribed_streamer_ids = set()
				for subscriber in self.subscription_manager.subscribers.values():
					for streamer_id in subscriber.subscribed_streamers:
						subscribed_streamer_ids.add(streamer_id)

				# Get all titles
				new_titles = dict()
				for streamer_id in subscribed_streamer_ids:
					new_titles[streamer_id] = self.twitch_client.get_stream_title(streamer_id)

				# Check titles for differences and notify subscribers
				for streamer_id, new_title in new_titles.items():
					# Ignore if there was no previously remembered title
					if titles.get(streamer_id) is None:
						continue

					# Ignore if the title did not change
					if new_title == titles.get(streamer_id):
						continue

					# TODO: Make configurable
					# Ignore if the streamer is live
					if self.twitch_client.is_live(streamer_id):
						continue

					# Notify all subscribers of that streamer
					for subscriber in self.subscription_manager.subscribers.values():
						if streamer_id in subscriber.subscribed_streamers:
							user = await self.client.fetch_user(subscriber.discord_id)

							embed = discord.Embed(
								title=f"Title update for *{self.twitch_client.get_display_name(streamer_id)}*",
								description=escape_markdown(new_title),
								color=discord.Color.orange()
							)
							await user.send(embed=embed)

				titles = new_titles
				await asyncio.sleep(self.config.scan_interval)

		@self.client.event
		async def on_ready():
			logging.info(f"bot is now logged in as {self.client.user}")
			asyncio.ensure_future(twitch_watcher())

		@self.client.event
		async def on_message(message):
			# ignore own messages
			if message.author == self.client.user:
				return

			# Ignore non-dms
			if not isinstance(message.channel, discord.DMChannel):
				return

			match = DiscordClient.COMMAND_REGEX.match(message.content)
			if not match:
				embed = discord.Embed(
					title="Help",
					description="You provided an invalid command. Get some help.",
					color=discord.Color.red()
				)
				embed.set_thumbnail(
					url="https://pm1.narvii.com/6870/7cff25068982d923c2b17cc2159373ac29e5d275r1-723-691v2_uhq.jpg"
				)
				embed.add_field(
					name="`!subscribe <twitch_channel_name>`",
					value="Subscribe to notifications for a new streamer.",
					inline=False
				)
				embed.add_field(
					name="`!unsubscribe <twitch_channel_name>`",
					value="Unsubscribe from notifications for a streamer.",
					inline=False
				)
				embed.add_field(
					name="`!subscriptions`",
					value="Show all your active subscriptions.",
					inline=False
				)
				await message.channel.recipient.send(embed=embed)
				return

			def sub_unsub_wrapper(func, name: str, subscriber: discord.User):
				try:
					_streamer_info = self.twitch_client.get_streamer_info_from_name(name)
				except IndexError:
					# No broadcaster returned.
					raise InputError(
						f"User '{DiscordClient.discord_user_to_full_name(subscriber)}'"
						f"tried to subscribe to nonexisting streamer."
						f"Sorry, this streamer does not exist."
					)

				return func(_streamer_info["id"], subscriber), _streamer_info

			# Dispatch commands
			try:
				response = "No message was prepared. Please inform my programmer."
				command = match.group(1)
				if command == "subscribe":
					success, streamer_info = sub_unsub_wrapper(
						self.subscription_manager.add_subscription,
						match.group(2),
						message.channel.recipient
					)

					if success:
						response = f"Successfully subscribed to updates for {streamer_info['display_name']}."
					else:
						response = f"You are already subscribed to {streamer_info['display_name']}."

				elif command == "unsubscribe":
					success, streamer_info = sub_unsub_wrapper(
						self.subscription_manager.remove_subscription,
						match.group(2),
						message.channel.recipient
					)

					if success:
						response = f"Successfully removed subscription for {streamer_info['display_name']}."
					else:
						response = f"You are not subscribed to {streamer_info['display_name']}."

				elif command == "subscriptions":
					data = self.subscription_manager.get_subscriptions(message.channel.recipient)
					if not data:
						response = "You do not have any subscriptions."
					else:
						names = [self.twitch_client.get_display_name(streamer_id) for streamer_id in data]
						response = f"You are subscribed to {' and '.join(', '.join(names).rsplit(', ', 1))}."

				await message.channel.recipient.send(response)

			except InputError as e:
				await message.channel.recipient.send(e.user_message)
				logging.info(e.log_message)

			except Exception as e:
				await message.channel.recipient.send("Sorry, an error has occured. Please contact my programmer.")
				logging.error(e)

		self.client.run(discord_bot_token, log_handler=None)

	@staticmethod
	def discord_user_to_full_name(user: discord.User):
		return f"{user.display_name}#{user.discriminator}"


def setup_logging(verbose: bool, debug: bool):
	if debug:
		log_level = logging.DEBUG
	elif verbose:
		log_level = logging.INFO
	else:
		log_level = logging.WARNING

	log_format = "[%(asctime)s] %(levelname)8s: %(message)s"
	log_date_format = "%Y-%m-%d %H:%M:%S"

	logging.basicConfig(level=log_level, format=log_format, datefmt=log_date_format)


def main():
	parser = argparse.ArgumentParser(
		description="Allows to subscribe for discord notification messages on multiple twitch stream state changes."
	)
	parser.add_argument("--verbose", "-v", action="store_true", help="Enable informational output.")
	parser.add_argument("--debug", "-d", action="store_true", help="Enable debug output.")
	parser.add_argument(
		"--config-path", "-c", default="config.json",
		help="The path to the configuration file, default is 'config.json' in the current working directory."
	)
	args = parser.parse_args()

	setup_logging(args.verbose, args.debug)

	with open(args.config_path, "r") as fh:
		config = json.load(fh)

	twitch_client = TwitchClient(config.get("twitch_client_id"), config.get("twitch_client_secret"))

	discord_config = DiscordClientConfig(config.get("scan_interval"), config.get("subscriptions_file"))
	DiscordClient(config.get("discord_bot_token"), twitch_client, discord_config)


if __name__ == "__main__":
	main()
