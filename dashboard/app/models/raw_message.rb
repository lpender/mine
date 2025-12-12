class RawMessage < ApplicationRecord
  self.table_name = "raw_messages"

  scope :from_channel, ->(channel) { where(channel: channel) }
  scope :recent, -> { order(message_timestamp: :desc) }
  scope :in_range, ->(start_time, end_time) { where(message_timestamp: start_time..end_time) }
end
