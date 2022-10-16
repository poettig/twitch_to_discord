import asyncio
import collections
import logging
import os
import re

import inflect
import discord

from nightbot_client import NightbotClient
from subscriptions import SubscriptionManager
from twitch_client import TwitchClient


class InputError(Exception):
	def __init__(self, log_message, user_message="Sorry, an error has occured. Please contact my programmer."):
		self.user_message = user_message
		self.log_message = log_message
		super.__init__(self.log_message)


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
		intents = discord.Intents.default()
		intents.guilds = True
		intents.messages = True
		intents.message_content = True
		self.client = discord.Client(intents=intents)
		self.twitch_client = twitch_client
		self.nightbot_client = NightbotClient()
		self.config = config

		if os.path.isfile(self.config.subscriptions_file_path):
			self.subscription_manager = SubscriptionManager.load_from_file(self.config.subscriptions_file_path)
		else:
			self.subscription_manager = SubscriptionManager(self.config.subscriptions_file_path)

		@self.client.event
		async def on_ready():
			logging.info(f"bot is now logged in as {self.client.user}")
			asyncio.ensure_future(self.watcher())

		@self.client.event
		async def on_message(message):
			await self.on_discord_message(message)

		self.client.run(discord_bot_token, log_handler=None)

	async def on_discord_message(self, message):
		# ignore own messages
		if message.author == self.client.user:
			return

		# Ignore non-dms
		if not isinstance(message.channel, discord.DMChannel):
			return

		match = DiscordClient.COMMAND_REGEX.match(message.content)
		recipient = message.author
		if not match:
			return await self.send_help(recipient)

		# Dispatch commands
		try:
			response = "No message was prepared. Please inform my programmer."
			command = match.group(1)

			if command == "subscribe":
				response = await self.subscribe_command(match, recipient)

			elif command == "unsubscribe":
				response = await self.unsubscribe_command(match, recipient)

			elif command == "subscriptions":
				response = await self.list_subscriptions_command(recipient)

			else:
				await self.send_help(recipient)
				return

			await recipient.send(response)
			logging.info(f"Sent {command} result to {recipient.name}.")

		except InputError as e:
			await recipient.send(e.user_message)
			logging.warning(e.log_message)

		except Exception as e:
			await recipient.send("Sorry, an error has occured. Please contact my programmer.")
			logging.error(e)
			raise e

	@staticmethod
	async def send_help(recipient):
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
		await recipient.send(embed=embed)
		logging.info(f"Sent help to {recipient.name}.")
		return

	async def sub_unsub_wrapper(self, func, name: str, subscriber: discord.User):
		try:
			_streamer_info = self.twitch_client.get_streamer_info_from_name(name)
		except IndexError:
			# No broadcaster returned.
			raise InputError(
				f"User '{DiscordClient.discord_user_to_full_name(subscriber)}'"
				f"tried to subscribe to nonexisting streamer.",
				f"Sorry, this streamer does not exist."
			)

		return func(_streamer_info["id"], subscriber), _streamer_info

	async def list_subscriptions_command(self, recipient):
		data = self.subscription_manager.get_subscriptions_by_discord_user(recipient)
		if not data:
			response = "You do not have any subscriptions."
		else:
			names = [self.twitch_client.get_display_name(streamer_id) for streamer_id in data]
			response = f"You are subscribed to {' and '.join(', '.join(names).rsplit(', ', 1))}."
		return response

	async def unsubscribe_command(self, match, recipient):
		success, streamer_info = self.sub_unsub_wrapper(
			self.subscription_manager.remove_subscription,
			match.group(2),
			recipient
		)
		if success:
			response = f"Successfully removed subscription for {streamer_info['display_name']}."
			logging.info(f"Removed subscription for {streamer_info['display_name']} from {recipient.name}.")
		else:
			response = f"You are not subscribed to {streamer_info['display_name']}."
		return response

	async def subscribe_command(self, match, recipient):
		success, streamer_info = self.sub_unsub_wrapper(
			self.subscription_manager.add_subscription,
			match.group(2),
			recipient
		)
		if success:
			response = f"Successfully subscribed to updates for {streamer_info['display_name']}."
			logging.info(f"Added subscription for {streamer_info['display_name']} to {recipient.name}.")
		else:
			response = f"You are already subscribed to {streamer_info['display_name']}."

		return response

	async def watcher(self):
		titles = dict()
		nightbot_subscribed_commands_messages = collections.defaultdict(dict)

		while True:
			logging.debug("Checking twitch for updates...")

			# Collect set of all subscribed streamers
			subscribed_streamer_ids = set()
			for subscriber in self.subscription_manager.subscribers.values():
				for streamer_id in subscriber.subscribed_streamers:
					subscribed_streamer_ids.add(streamer_id)

			# Get all titles
			new_titles = dict()
			for streamer_id in subscribed_streamer_ids:
				new_titles[streamer_id] = self.twitch_client.get_stream_title(streamer_id)

			# Check titles for changes
			for streamer_id, new_title in new_titles.items():
				# Ignore if there was no previously remembered title
				if titles.get(streamer_id) is None:
					logging.debug(f"No tracked title available for {self.twitch_client.get_display_name(streamer_id)}.")
					continue

				# Ignore if the title did not change
				if new_title == titles.get(streamer_id):
					logging.debug(f"Title did not change for {self.twitch_client.get_display_name(streamer_id)}.")
					continue

				# TODO: Make configurable
				# Ignore if the streamer is live
				if self.twitch_client.is_live(streamer_id):
					logging.info(f"{self.twitch_client.get_display_name(streamer_id)} is already live, not sending update.")
					continue

				# Notify all subscribers of that streamer
				await self.notify_subscribers(
					streamer_id, "Title update", new_title,
					"https://pbs.twimg.com/profile_images/1450901581876973568/0bHBmqXe_400x400.png"
				)

			logging.debug("Checking nightbot for updates...")

			# Check subscribed nightbot commands for changes
			for streamer_id in subscribed_streamer_ids:
				# Get commands
				streamer_login_name = self.twitch_client.get_login_name(streamer_id)
				commands_new_state = self.nightbot_client.get_commands_by_channel_name(streamer_login_name)

				# TODO: Make configurable
				# Get message for subscribed commands
				filtered_new_commands_state = list(filter(lambda entry: entry.get('name') == "!plan", commands_new_state))
				if len(filtered_new_commands_state) == 0:
					# Command does not exist for streamer, ignore
					continue

				command_message = filtered_new_commands_state[0].get("message")
				if command_message is None:
					raise ValueError("No message in nightbot command entry, something is extremely wrong!")

				# If there is no remembered command message, remember this one and continue
				if nightbot_subscribed_commands_messages.get(streamer_id) is None:
					nightbot_subscribed_commands_messages[streamer_id]["!plan"] = command_message
					continue

				# Check if the message of the subscribed command changed
				if command_message != nightbot_subscribed_commands_messages.get(streamer_id).get("!plan"):
					await self.notify_subscribers(
						streamer_id, "Nightbot command !plan changed", command_message,
						"https://pbs.twimg.com/profile_images/788218320398917633/ssK-yqxf_400x400.jpg"
					)

				# Update remembered command messages
				nightbot_subscribed_commands_messages[streamer_id]["!plan"] = command_message

			titles = new_titles

			await asyncio.sleep(self.config.scan_interval)

	async def notify_subscribers(
		self,
		streamer_id: int,
		notification_title_prefix: str,
		notification_description: str,
		notification_thumbnail_url: str = None
	):
		streamer_display_name = self.twitch_client.get_display_name(streamer_id)
		for subscriber in self.subscription_manager.subscribers.values():
			if streamer_id in subscriber.subscribed_streamers:
				user = await self.client.fetch_user(subscriber.discord_id)

				embed = discord.Embed(
					title=f"{notification_title_prefix} for *{self.escape_markdown(streamer_display_name)}*",
					description=self.escape_markdown(notification_description),
					color=discord.Color.orange()
				)

				if notification_thumbnail_url:
					embed.set_thumbnail(url=notification_thumbnail_url)

				await user.send(embed=embed)
				logging.debug(f"Sent notification '{notification_title_prefix}' for {streamer_display_name} to {user.name}")

		logging.info(
			f"Sent notification '{notification_title_prefix}' for {streamer_display_name}"
			f" to {self.subscription_manager.get_subscriber_count()}"
			f" {inflect.engine().plural('subscriber', self.subscription_manager.get_subscriber_count())}"
		)

	@staticmethod
	def discord_user_to_full_name(user: discord.User):
		return f"{user.display_name}#{user.discriminator}"

	@staticmethod
	def escape_markdown(text: str):
		return re.sub(r"([_~*>`|])", r"\\\1", text)
