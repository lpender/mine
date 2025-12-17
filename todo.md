todo

*** Data Issues ***

[ ] 2025-12-12 08:01 data for YCBD is wrong.
[ ] 2025-12-11 11:22 data for GRAN is wrong.
[ ] 2025-12-12 11:10 data for NCPL is wrong.
[ ] 2025-12-12 11:10 data for NCPL is wrong.
[ ] 2025-12-12 05:25 data for NCI is wrong.

*** Trading Issues ***

[ ] orphaned unfilled buy orders

[ ] orphaned unfilled sell orders

[ ] liquidate all positions at end of day

[ ] filters for 3 green bars 10m, NHOD, NSH, etc.

[ ] upgrade to use alpaca ~~massive~~ for websockets etc.

[ ] Three volume confirmation strategies:
       eager (the moment that it appears it will be met, e.g. if it's 1/10 of the way into the minute and 1/10th of the way into volume requirement).
       medium (the moment that full confirmation is met on the bar)
       cautious (when the bar is complete and the volume is met)

[ ] little slow to enter a position?

[ ] finish implementing trading_history ui with tradingview charts

[ ] sell entire position at end of day OR track only "extended market minutes" not when it's closed

[ ] position multiplier based on how 'hot' the market is (s&p)

done

[x] trigger to only trade a symbol once a day

[x] when candle is building green -- execute as soon as it hits 100% of the volume threshold.
    don't wait for the next candle.

[x] let's randomly spot check OHLCV data for 10 or so symbols, I'll verify on my
    end and then let's write a regression test with that exact data.

[x] Let's do that offset (so that the candles show the minute the OHLCV ended
    rather than began). Then I think we'll be missing a lot of data. Do we need to
    refetch everything?

[x] It's still attempting to subscribe to 13 symbols even though the limit is 5...
    you need to reject new symbols if you're over the limit.

[x] It's buying 50 worth of a stock no matter what

[x] it seems to be holding UDMY for several hours even though it was only supposed to have been 25 minutes.

[x] restarting the server seems to have stopped the monitoring

10:40:07 [MIGI] CANDLE BUILDING: RED | Vol: 100 / 100,000 (0%) | O=4.84 C=4.84
10:40:07 [MIGI] LAST COMPLETED CANDLE: RED | Vol: 10,910 <✗ 100,000 | Completed green candles with vol: 0/1 needed
10:40:08 [MIGI] QUOTE: $4.8400 vol=300 | filter: $4.50-$17.00
10:40:08 [MIGI] CANDLE BUILDING: RED | Vol: 400 / 100,000 (0%) | O=4.84 C=4.84
10:40:08 [MIGI] LAST COMPLETED CANDLE: RED | Vol: 10,910 <✗ 100,000 | Completed green candles with vol: 0/1 needed

[x] Let's also save the filters in a querystring for /trade_history

[/] announcements within 120 minutes are having weird fetch issues. (need to verify this is fixed going forwards)

[x] upload the guide to an md file

[x] show detailed reason for entry exit in trade_history ui

[x] order events still display wrong although they are correctly ordered. see trade 149

2025-12-17 07:51:56
2025-12-17 12:51:57
2025-12-17 07:57:17
2025-12-17 12:57:18

[x] strategy to check volume of previous 1 minute bar in the volume trigger.
    get a 1 min bar when we start watching a quote and use that as volume trigger

[x] start tracking announcements in the database. separate table or shared?

[x] traces (news rec'd, purchased, sold, etc.)
