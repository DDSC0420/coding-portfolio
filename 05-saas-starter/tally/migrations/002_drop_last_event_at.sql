-- Webhooks no longer order events by their `created` time (whole seconds, so two can tie): each
-- event now makes the app fetch the subscription from the provider and store that. The column
-- that held the last applied event's time has no use left.
ALTER TABLE subscriptions DROP COLUMN last_event_at;
