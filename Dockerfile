# Use official Node.js 18 image
FROM node:18

# Create app directory
WORKDIR /app

# Copy bot files
COPY . .

# Install dependencies
RUN npm install discord.js

# Expose port (if needed)
EXPOSE 3000

# Start bot
CMD ["node", "index.js"]
