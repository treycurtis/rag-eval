
output "bucket_name" {
  description = "Name of raw papers S3 bucket"
  value       = aws_s3_bucket.raw_papers.bucket
}

output "bucket_arn" {
  description = "ARN of raw papers S3 bucket"
  value       = aws_s3_bucket.raw_papers.arn
}

output "ingestion_role_arn" {
  description = "ARN of the ingestion IAM role"
  value       = aws_iam_role.ingestion.arn
}