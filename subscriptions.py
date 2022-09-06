import json
import logging
import typing

import discord

import discord_client
import inflect


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

	def get_subscribed_streamer_count(self):
		return len(self.subscribed_streamers)

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

		logging.info(f"Loaded subscriptions for {len(subscribers)} {inflect.engine().plural('subscriber', len(subscribers))}.")

		return SubscriptionManager(subscriptions_file_path, subscribers)

	def dump_to_file(self, filename: str):
		subscribers = []
		for subscriber in self.subscribers.values():
			subscribers.append(subscriber.to_dict())

		with open(filename, "w") as fh:
			json.dump(subscribers, fh)

		logging.info(f"Wrote subscriptions for {len(subscribers)} {inflect.engine().plural('subscriber', len(subscribers))}.")

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

	def get_subscriptions_by_discord_user(self, subscriber: discord.User):
		data = self.subscribers.get(subscriber.id)
		if data is None:
			return False

		return data.subscribed_streamers

	def get_subscriber_count(self):
		return len(self.subscribers)
