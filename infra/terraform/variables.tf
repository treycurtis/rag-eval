variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "bucket_name" {
  description = "S3 bucket name for raw arXiv papers"
  type        = string
  default     = "rag-eval-papers-raw"
}

variable "project" {
  description = "Project name used for tagging"
  type        = string
  default     = "rag-eval"
}