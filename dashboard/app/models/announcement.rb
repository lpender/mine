class Announcement < ApplicationRecord
  self.table_name = "announcements"

  has_many :ohlcv_bars, foreign_key: [:announcement_ticker, :announcement_timestamp],
                        primary_key: [:ticker, :timestamp]

  scope :premarket, -> { where("EXTRACT(HOUR FROM timestamp) >= 4 AND EXTRACT(HOUR FROM timestamp) < 9 OR (EXTRACT(HOUR FROM timestamp) = 9 AND EXTRACT(MINUTE FROM timestamp) < 30)") }
  scope :market, -> { where("(EXTRACT(HOUR FROM timestamp) = 9 AND EXTRACT(MINUTE FROM timestamp) >= 30) OR (EXTRACT(HOUR FROM timestamp) >= 10 AND EXTRACT(HOUR FROM timestamp) < 16)") }
  scope :postmarket, -> { where("EXTRACT(HOUR FROM timestamp) >= 16 AND EXTRACT(HOUR FROM timestamp) < 20") }

  scope :with_headline, -> { where.not(headline: [nil, ""]) }
  scope :financing, -> { where(headline_is_financing: true) }
  scope :not_financing, -> { where(headline_is_financing: [false, nil]) }

  scope :us_only, -> { where(country: "US") }
  scope :recent, -> { order(timestamp: :desc) }
  scope :with_source, -> { where.not(source_message: [nil, ""]) }

  def market_session
    hour = timestamp.hour
    minute = timestamp.min

    if hour >= 4 && (hour < 9 || (hour == 9 && minute < 30))
      "premarket"
    elsif (hour == 9 && minute >= 30) || (hour >= 10 && hour < 16)
      "market"
    elsif hour >= 16 && hour < 20
      "postmarket"
    else
      "closed"
    end
  end
end
