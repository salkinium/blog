rm -rf _site/ docs/
bundle exec jekyll build
mv _site docs/
echo "blog.salkinium.com" > docs/CNAME
git add docs/
