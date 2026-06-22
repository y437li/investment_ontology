import axios from 'axios'

// Create axios instance mirroring MiroFish pattern
const service = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  timeout: 300000, // 5 minutes — pipeline steps can be slow
  headers: {
    'Content-Type': 'application/json'
  }
})

// Request interceptor
service.interceptors.request.use(
  config => config,
  error => {
    console.error('Request error:', error)
    return Promise.reject(error)
  }
)

// Response interceptor
service.interceptors.response.use(
  response => response.data,
  error => {
    console.error('Response error:', error)
    return Promise.reject(error)
  }
)

// Retry helper (mirrors MiroFish)
export const requestWithRetry = async (requestFn, maxRetries = 2, delay = 1000) => {
  for (let i = 0; i < maxRetries; i++) {
    try {
      return await requestFn()
    } catch (error) {
      if (i === maxRetries - 1) throw error
      console.warn(`Request failed, retrying (${i + 1}/${maxRetries})...`)
      await new Promise(resolve => setTimeout(resolve, delay * Math.pow(2, i)))
    }
  }
}

export default service
