-- Add schedule columns for chronological itinerary rendering.
alter table public.itinerary_nodes
    add column if not exists date_local date,
    add column if not exists start_time_local time without time zone,
    add column if not exists end_time_local time without time zone;

create index if not exists idx_itinerary_nodes_trip_date_start
    on public.itinerary_nodes (trip_id, date_local, start_time_local);
