-- CreateEnum
CREATE TYPE "RunStatus" AS ENUM ('PENDING', 'PROCESSING', 'COMPLETE', 'FAILED');

-- CreateTable
CREATE TABLE "User" (
    "id" TEXT NOT NULL,
    "auth0Id" TEXT NOT NULL,
    "email" TEXT NOT NULL,
    "name" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "User_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Run" (
    "id" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "status" "RunStatus" NOT NULL DEFAULT 'PENDING',
    "goal" TEXT,
    "urlA" TEXT,
    "urlB" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "Run_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "RunVersion" (
    "id" TEXT NOT NULL,
    "runId" TEXT NOT NULL,
    "version" TEXT NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "RunVersion_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "RunSummary" (
    "id" TEXT NOT NULL,
    "runId" TEXT NOT NULL,
    "winner" TEXT NOT NULL,
    "summaryText" TEXT NOT NULL,
    "avgRoiA" JSONB NOT NULL,
    "avgRoiB" JSONB NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "RunSummary_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "RunInsight" (
    "id" TEXT NOT NULL,
    "runId" TEXT NOT NULL,
    "version" TEXT NOT NULL,
    "timestampStart" DOUBLE PRECISION NOT NULL,
    "timestampEnd" DOUBLE PRECISION NOT NULL,
    "uxObservation" TEXT NOT NULL,
    "recommendation" TEXT NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "RunInsight_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "User_auth0Id_key" ON "User"("auth0Id");

-- CreateIndex
CREATE UNIQUE INDEX "User_email_key" ON "User"("email");

-- CreateIndex
CREATE INDEX "Run_userId_createdAt_idx" ON "Run"("userId", "createdAt" DESC);

-- CreateIndex
CREATE INDEX "RunVersion_runId_idx" ON "RunVersion"("runId");

-- CreateIndex
CREATE UNIQUE INDEX "RunVersion_runId_version_key" ON "RunVersion"("runId", "version");

-- CreateIndex
CREATE UNIQUE INDEX "RunSummary_runId_key" ON "RunSummary"("runId");

-- CreateIndex
CREATE INDEX "RunInsight_runId_version_idx" ON "RunInsight"("runId", "version");

-- AddForeignKey
ALTER TABLE "Run" ADD CONSTRAINT "Run_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "RunVersion" ADD CONSTRAINT "RunVersion_runId_fkey" FOREIGN KEY ("runId") REFERENCES "Run"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "RunSummary" ADD CONSTRAINT "RunSummary_runId_fkey" FOREIGN KEY ("runId") REFERENCES "Run"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "RunInsight" ADD CONSTRAINT "RunInsight_runId_fkey" FOREIGN KEY ("runId") REFERENCES "Run"("id") ON DELETE CASCADE ON UPDATE CASCADE;
