// Import your freshly minted closeup asset safely
import techbearImg from '../assets/TechbearCloseup-NoBG.png'; 

export default function TechbearAvatar({ size = 'md', border = true }) {
  // Dynamic size mapping to keep the component reusable across 
  // the Submit form, the Dashboard, and the Slideshow!
  const sizeClasses = {
    sm: 'w-10 h-10',
    md: 'w-20 h-20',
    lg: 'w-32 h-32',
  };

  return (
    <div className="flex items-center justify-center">
      <div 
        className={`
          ${sizeClasses[size] || sizeClasses.md}
          rounded-full 
          overflow-hidden 
          object-cover
          ${border ? 'border-4 border-amber-500 shadow-md' : ''}
          bg-transparent
          transition-transform 
          duration-300 
          hover:scale-105
        `}
      >
        <img 
          src={techbearImg} 
          alt="TechBear Avatar" 
          className="w-full h-full object-cover"
          // Smooth fallback if the image path encounters issues
          onError={(e) => {
            e.target.onerror = null;
            e.target.src = 'https://ui-avatars.com/api/?name=Tech+Bear&background=F59E0B&color=fff';
          }}
        />
      </div>
    </div>
  );
}