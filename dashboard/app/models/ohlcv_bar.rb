class OhlcvBar < ApplicationRecord
  self.table_name = "ohlcv_bars"

  belongs_to :announcement, foreign_key: [:announcement_ticker, :announcement_timestamp],
                            primary_key: [:ticker, :timestamp], optional: true

  scope :for_ticker, ->(ticker) { where(ticker: ticker) }
  scope :in_range, ->(start_time, end_time) { where(timestamp: start_time..end_time) }
  scope :chronological, -> { order(timestamp: :asc) }

  def green?
    close > open
  end

  def red?
    close < open
  end

  def range
    high - low
  end

  def body
    (close - open).abs
  end
end
