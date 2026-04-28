
with source as (
    select * from {{source('raw', 'arxiv_papers')}}
),

processed as (

    select
        paper_id,
        arxiv_url,
        title,
        abstract,
        authors, 
        categories,
        published_date::timestamp_ntz as published_date,
        coalesce(ingested_at,current_timestamp())::timestamp_ntz as ingested_date
    from source
)

select * from processed