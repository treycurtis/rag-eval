
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
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
    Project     = "rag-eval"
    Environment = "dev"
  }
}

resource "aws_s3_bucket_versioning" "raw_papers" {
  bucket = aws_s3_bucket.raw_papers.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "raw_papers" {
  bucket = aws_s3_bucket.raw_papers.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_iam_role" "ingestion" {
  name = "rag-eval-ingestion-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Project     = "rag-eval"
    Environment = "dev"
  }
}

resource "aws_iam_role_policy" "ingestion_s3" {
  name = "rag-eval-ingestion-s3-policy"
  role = aws_iam_role.ingestion.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.raw_papers.arn,
          "${aws_s3_bucket.raw_papers.arn}/*"
        ]
      }
    ]
  })
}