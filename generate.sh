rm -rf _site/
bundle exec jekyll build && rm -rf docs/
cp -r _site docs/
echo "blog.salkinium.com" > docs/CNAME
git add docs/
