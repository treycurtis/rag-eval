
terraform {
    required_providers {
        aws = {
            source = "hashicorp/aws"
            version = "~> 5.0"
        }
    }
}

provider "aws" {
    region = var.aws_region
}

resource "aws_s3_bucket" "raw_papers" {
    bucket = var.bucket_name

    tags = {
        Project =     "rag-eval"
        Environment = "dev"
    }
}

resource "aws_s3_bucket_versioning" "raw_papers" {
    bucket = aws_s3_bucket.raw_papers.id

    versioning_configuration {
        status = "Disabled"
    }
}

resource "aws_s3_bucket_public_access_block" "raw_papers" {
    bucket = aws_s3_bucket.raw_papers.id

    block_public_acls = true
    block_public_policy = true
    ignore_public_acls = true
    restrict_public_buckets = true
}